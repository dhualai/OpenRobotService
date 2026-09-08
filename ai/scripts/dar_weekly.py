# -*- coding: utf-8 -*-
"""直答率周流程单入口：export → prepare → l1 → retrieval → l3 → tool → report。

每周流程（步骤名按需组合，缺省全跑除 export 外的本地步骤）：
  export     ssh 到测试服务器导出四表 csv.gz → export_dar/（凭据只在服务器端解析，
             不回传不落日志；首次跑或 ssh key 不在时先手动验证 ssh 通）
  prepare    csv.gz → processed/conversations_split.jsonl（dar_prepare）
  l1         LLM 批判 + L1/L2 统计（dar_l1 --replay 不存在时自动跑批判）
  retrieval  全段检索判定（dar_retrieval_check，增量：已判段复用）
  l3         全段四类预标（dar_l3 --all，供标注工具注入）
  tool       生成 segmentation_tool.html（build_segmentation_tool）
  report     聚合周报：L1/L2 + 检索交叉 + 预标分布 + 人工标注进度 + 与上周对比，
             落盘 processed/weekly_YYYYMMDD.json

用法：
  python ai/scripts/dar_weekly.py export prepare l1 retrieval l3 tool report
  python ai/scripts/dar_weekly.py report      # 只重出报告
"""
import glob
import io
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime as _dt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = r"C:/Users/PAJ26020/Desktop/export_dar"
OUT = os.path.join(DATA, "processed")
MANUAL = r"C:/Users/PAJ26020/Downloads/manual_segmentation.json"
SSH_HOST = "usp-a@125.122.97.107"
SSH_PORT = "8802"
REMOTE_PY = "~/miniconda3/envs/test-ai/bin/python"
REMOTE_ENV = "/data/apps/TestOpenRobotService/ai/.env"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")

# 四表导出列（与 dar_prepare.load 的读取字段对齐；列名=服务器库实际列名）
EXPORT_TABLES = {
    "users": "id,username,name",
    "conversations": "id,user_id,title,created_at,service_ticket_id,metadata_",
    "messages": "id,conversation_id,role,message_type,sequence,created_at,content",
    "tasks": ("id,title,task_type,status,created_by,project_name,source,"
              "external_id,created_at,metadata_info"),
}

REMOTE_EXPORT = r'''
import csv, gzip, io as _io, os, re, sys
import pymysql

env = open({env!r}, encoding="utf-8").read()
url = next(l for l in env.splitlines() if l.startswith("DATABASE_URL="))
m = re.search(r"//([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/(\w+)", url)
user, pwd, host, port, db = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5)
conn = pymysql.connect(host=host, port=port, user=user, password=pwd,
                       database=db, charset="utf8mb4")
os.makedirs("/tmp/dar_export", exist_ok=True)
for name, cols in {tables!r}.items():
    with conn.cursor() as cur, gzip.open(f"/tmp/dar_export/{{name}}.csv.gz",
                                         "wt", encoding="utf-8", newline="") as fh:
        cur.execute(f"SELECT {{cols}} FROM {{name}}")
        w = csv.writer(fh)
        w.writerow(cols.split(","))
        n = 0
        for row in cur:
            w.writerow(["" if v is None else str(v) for v in row])
            n += 1
    print(f"{{name}}: {{n}} rows")
conn.close()
print("EXPORT_OK")
'''.format(env=REMOTE_ENV, tables=EXPORT_TABLES)


def sh(cmd, **kw):
    print(f"$ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}")
    return subprocess.run(cmd, check=True, **kw)


def step_export():
    remote_cmd = f"{REMOTE_PY} - <<'DARPYEOF'\n{REMOTE_EXPORT}\nDARPYEOF"
    sh(["ssh", "-p", SSH_PORT, SSH_HOST, remote_cmd])
    sh(["scp", "-P", SSH_PORT, f"{SSH_HOST}:/tmp/dar_export/*.csv.gz", DATA + "/"])
    print(f"导出落位 {DATA}/（四表 csv.gz）")


def step(name, script, args=()):
    print(f"\n{'=' * 72}\n== {name}：{script} {' '.join(args)}\n{'=' * 72}")
    sh([sys.executable, os.path.join(HERE, script), *args])


def _same_denominator_compare(judge_rows):
    """同分母对比：基准=真实组人工已标段（直答正确/未直答/未覆盖，剔除直接提单）。
    L1=段内未出单占比（基础公式段级投影）；L2=人工直答正确占比（端到端口径）；
    L3=AI 预标直答正确占比（AI 判成直接提单的段留在分母但不得分——惩罚偏宽）。"""
    if not os.path.exists(MANUAL) or not os.path.exists(SPLIT):
        return None
    # AI 预标按段归属：--all 预标用 AI topic 切分（段首≠人工段首），
    # 人工段取重叠回合数最多的 AI 段的 pre（段末=下一段首，尾段放开）
    tmp = {}
    for r in judge_rows:
        if r.get("astart") is not None:
            tmp.setdefault(str(r["cid"]), []).append(
                (int(r["astart"]), r.get("pre")))
    pre_segs = {}
    for cid, lst in tmp.items():
        lst.sort()
        n = len(lst)
        pre_segs[cid] = [(lst[i][0], lst[i + 1][0] if i + 1 < n else 10 ** 9,
                          lst[i][1]) for i in range(n)]

    def ai_pre(cid, s0, e):
        best, best_ov = None, 0
        for a_s, a_e, p in pre_segs.get(cid, ()):
            ov = min(e, a_e) - max(s0, a_s)
            if ov > best_ov:
                best_ov, best = ov, p
        return best

    def pts(x):
        try:
            return _dt.fromisoformat((x or "").split(".")[0])
        except ValueError:
            return None

    convs = {str(c["conversation_id"]): c for c in
             (json.loads(l) for l in open(SPLIT, encoding="utf-8"))}
    cls_by = {str(j["conversation_id"]): j["cls"] for j in
              (json.loads(l) for l in open(
                  os.path.join(OUT, "conversations_classified.jsonl"),
                  encoding="utf-8"))}
    man = json.load(open(MANUAL, encoding="utf-8"))
    labels, bounds = man.get("labels") or {}, man.get("bounds") or {}
    legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
    n = l1 = l2 = l3 = 0
    for cid, lm in labels.items():
        c = convs.get(cid)
        cl = cls_by.get(cid)
        if not c or not cl or len(cl) != len(c["rounds"]) or c.get("is_tester"):
            continue
        rounds = c["rounds"]
        manual = sorted({0, *(int(x) for x in (bounds.get(cid) or [])
                              if 0 <= int(x) < len(rounds))})
        tasks = [pts(t.get("at")) for t in c.get("tasks") or []]
        for tid, s0 in enumerate(manual):
            lab = legacy.get(lm.get(str(s0)), lm.get(str(s0)))
            if lab not in ("直答正确", "未直答", "未覆盖"):
                continue
            e = manual[tid + 1] if tid + 1 < len(manual) else len(rounds)
            if not any(cl[i]["q"] and any(a.strip() for a in rounds[i]["a"])
                       for i in range(s0, e)):
                continue
            start = pts(rounds[s0]["at"])
            end = pts(rounds[e]["at"]) if e < len(rounds) else (
                pts(rounds[e - 1]["at"]).timestamp() + 1800 if e > s0 else None)
            ticketed = any(start and t and t >= start and
                           (t.timestamp() <= end if isinstance(end, float) else t <= end)
                           for t in tasks if t)
            n += 1
            l1 += 0 if ticketed else 1
            l2 += 1 if lab == "直答正确" else 0
            l3 += 1 if ai_pre(cid, s0, e) == "直答正确" else 0
    if not n:
        return None
    return {
        "base": f"{n} 段",
        "L1_段级": f"{l1 / n * 100:.1f}%（{l1}/{n}）",
        "L2_人工": f"{l2 / n * 100:.1f}%（{l2}/{n}）",
        "L3_AI同段": f"{l3 / n * 100:.1f}%（{l3}/{n}）",
    }


def step_report():
    def latest_one(pattern):
        files = sorted(glob.glob(os.path.join(OUT, pattern)))
        return files[-1] if files else ""

    rep = {"date": f"{_dt.now():%Y-%m-%d}"}

    # L1/L2：最新 summary json
    s_path = latest_one("direct_answer_summary_*.json")
    if s_path:
        s = json.load(open(s_path, encoding="utf-8"))
        rep["l1_source"] = os.path.basename(s_path)
        rep["l2_labels"] = s.get("l2_labels")
        all_month = "全期" if any("全期" in k for k in s["stats"]) else sorted(
            {k.split("|")[1] for k in s["stats"]})[-1]
        for g in ("真实组", "测试组"):
            v = s["stats"].get(f"{g}|{all_month}")
            if v:
                rep[f"l1_{g}"] = {k: v[k] for k in v if v[k]}

    # 检索交叉
    r_path = latest_one("retrieval_check_*.json")
    if r_path:
        rows = json.load(open(r_path, encoding="utf-8"))
        rep["retrieval_source"] = os.path.basename(r_path)
        cross = {}
        for r in rows:
            if r.get("verdict") in ("yes", "partial", "no"):
                cross.setdefault(r.get("grp", "?"), Counter())[r["verdict"]] += 1
        rep["retrieval"] = {g: dict(c) for g, c in cross.items()}
        rep["retrieval_no_rate"] = {
            g: round(c.get("no", 0) / sum(c.values()), 3) for g, c in cross.items()
            if sum(c.values())}

    # 预标分布 + 已标段一致率
    j_path = latest_one("l3_judge_all_*.json")
    if j_path:
        rows = json.load(open(j_path, encoding="utf-8"))
        rep["pre_source"] = os.path.basename(j_path)
        for g in ("真实组", "测试组"):
            sub = [r for r in rows if r.get("grp") == g]
            if sub:
                rep[f"pre_{g}"] = dict(Counter(r.get("pre", "?") for r in sub))
        hit = tot = 0
        for r in rows:
            if r.get("lab") and r["lab"] != "未标" and r.get("pre"):
                tot += 1
                hit += r["pre"] == r["lab"]
        if tot:
            rep["pre_agree_labeled"] = f"{hit}/{tot} = {hit / tot * 100:.0f}%"

    # 人工标注进度
    if os.path.exists(MANUAL):
        man = json.load(open(MANUAL, encoding="utf-8"))
        labels = man.get("labels") or {}
        bounds = man.get("bounds") or {}
        n_seg = sum(len(v) for v in bounds.values())
        n_lab = sum(1 for v in labels.values() for x in v.values()
                    if x and x != "未标")
        rep["manual_progress"] = f"{n_lab}/{n_seg} 段已标"

    # AI 判定 vs 人工标签（校准 judge：人工段考卷的四类混淆矩阵）
    cal_files = sorted(glob.glob(os.path.join(OUT, "l3_judge_[0-9]*.json")))
    if cal_files:
        cal_path = cal_files[-1]
        cal = json.load(open(cal_path, encoding="utf-8"))
        rv_seg = {}
        if r_path:
            for r in json.load(open(r_path, encoding="utf-8")):
                rv_seg[(str(r["cid"]), r.get("seg"))] = r.get("verdict")

        def quad(r):
            if r["intent"] == "ticket":
                return "直接提单"
            if r["intent"] == "consult" and r["resolved"] == "yes":
                return "直答正确"
            return "未覆盖" if rv_seg.get((str(r["cid"]), r.get("seg"))) == "no" else "未直答"

        labs = ["直接提单", "直答正确", "未直答", "未覆盖"]
        cm = Counter((r["lab"], quad(r)) for r in cal if r.get("lab") in labs)
        n = sum(cm.values())
        hit = sum(cm.get((l, l), 0) for l in labs)
        rep["ai_vs_manual"] = {
            "source": os.path.basename(cal_path),
            "agreement": f"{hit}/{n} = {hit / n * 100:.1f}%",
            "confusion": {l: {k2: cm.get((l, k2), 0) for k2 in labs} for l in labs},
        }
        print(f"\n== AI 判定 vs 人工标签（{os.path.basename(cal_path)}，{n} 段）==")
        print(f"对齐 {hit}/{n} = {hit / n * 100:.1f}%")
        print(f"  {'':8}" + "".join(f"{k:>6}" for k in labs) + "   召回")
        for l in labs:
            row = [cm.get((l, k2), 0) for k2 in labs]
            print(f"  人工{l:<5}" + "".join(f"{v:>6}" for v in row)
                  + f"   {cm.get((l, l), 0) / sum(row) * 100:.0f}%")

    # 直答率三口径对比（真实组）：基础公式 L1 / 人工 L2 / AI L3
    rates = {}
    if s_path:
        months = [v for k, v in s["stats"].items() if k.startswith("真实组|")]
        sq, st = sum(m["segs_q"] for m in months), sum(m["segs_ticket"] for m in months)
        cq, ct = sum(m["convs_q"] for m in months), sum(m["convs_ticket"] for m in months)
        if sq and cq:
            rates["L1_基础(1−转工单率)"] = (
                f"话题级 {(1 - st / sq) * 100:.1f}%（{sq - st}/{sq}）上界近似")
    l2 = ((s.get("l2_labels") or {}).get("真实组") if s_path else None) or {}
    ok, bad, unc = l2.get("直答正确", 0), l2.get("未直答", 0), l2.get("未覆盖", 0)
    if ok + bad:
        rates["L2_人工"] = (
            f"确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
            f"｜端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
            f"｜已标 {ok + bad + unc} 段")
    if j_path:
        sub = [r for r in rows if r.get("grp") == "真实组"]
        p = Counter(r.get("pre", "?") for r in sub)
        ok, bad, unc = p.get("直答正确", 0), p.get("未直答", 0), p.get("未覆盖", 0)
        if ok + bad:
            rates["L3_AI预标"] = (
                f"确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
                f"｜端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
                f"｜全段 {len(sub)}（judge 偏宽仅供参考）")
    if rates:
        rep["dar_rates"] = rates
        print("\n== 直答率三口径对比（真实组）==")
        for k, v in rates.items():
            print(f"  {k}：{v}")

    # 同分母对比：人工已标段为公共分母，三个口径同场（消除各自剔除规则的失真）
    same = _same_denominator_compare(rows if j_path else [])
    if same:
        rep["dar_rates_same_base"] = same
        print("\n== 同分母对比（基准=人工已标真实组段，剔除直接提单）==")
        print(f"  L1_段级(未出单即算直答)：{same['L1_段级']}")
        print(f"  L2_人工              ：{same['L2_人工']}")
        print(f"  L3_AI同段            ：{same['L3_AI同段']}")

    prevs = [p for p in sorted(glob.glob(os.path.join(OUT, "weekly_*.json")))
             if os.path.basename(p) != f"weekly_{_dt.now():%Y%m%d}.json"]
    if prevs:
        prev = json.load(open(prevs[-1], encoding="utf-8"))
        print(f"对比 {os.path.basename(prevs[-1])} → 本周变化：")
        for k in ("l1_真实组", "retrieval_no_rate", "manual_progress"):
            old, new = prev.get(k), rep.get(k)
            if old != new:
                print(f"  {k}: {old} → {new}")

    path = os.path.join(OUT, f"weekly_{_dt.now():%Y%m%d}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"周报: {path}")


STEPS = {
    "export": lambda: step_export(),
    "prepare": lambda: step("prepare", "dar_prepare.py"),
    # dar_l1 --out 默认 Desktop（非 processed），显式传；有人工标注带上 --review 出 L2
    "l1": lambda: step("l1", "dar_l1.py", ("--out", OUT) +
                       (("--review", MANUAL) if os.path.exists(MANUAL) else ())),
    "retrieval": lambda: step("retrieval", "dar_retrieval_check.py"),
    "l3": lambda: step("l3 预标", "dar_l3.py", ("--all",)),
    "tool": lambda: step("tool", "build_segmentation_tool.py"),
    "report": lambda: step_report(),
}


def main():
    names = sys.argv[1:]
    if not names:
        names = [n for n in STEPS if n != "export"]  # 缺省本地全流程
    bad = [n for n in names if n not in STEPS]
    if bad:
        sys.exit(f"未知步骤 {bad}；可用：{list(STEPS)}")
    for n in names:
        STEPS[n]()


if __name__ == "__main__":
    main()
