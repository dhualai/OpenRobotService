# -*- coding: utf-8 -*-
"""直答率周流程单入口：export → prepare → l1 → tool0 → l1r/retrieval/l3/tool → report。

每周流程（步骤名按需组合，缺省全跑除 export 外的本地步骤）：
  全流程    导数据(export prepare) → L1(l1) → 人工切题(浏览器改边界、保存到工作台
            =export_dar/{env}/manual_segmentation.json，自动重算 L1) → L3 预标
            (l1r retrieval l3 tool，按人工边界判) → 人工标注(浏览器打标签、保存到
            工作台) → 吸收出周报(l1r retrieval l3 report)。
            先人工定边界再判定：judge/检索重放的输入就是人工认可的话题段，
            标注轮预标全部有效。
  export     ssh 到测试服务器导出四表 csv.gz → export_dar/（凭据只在服务器端解析，
             不回传不落日志；首次跑或 ssh key 不在时先手动验证 ssh 通）
  prepare    csv.gz → processed/conversations_split.jsonl（dar_prepare）
  l1         LLM 批判 + L1 统计（有人工标注文件时带 --review 出 L2）
  tool0      生成切题版标注工具（--bounds-only：无预标，只定边界）
  l1r        L1 重算：--replay --review 吸收人工切分/标签（不调 LLM 秒出；
             无人工文件时跳过，判定产物落后于新导出时回退 l1 全量）
  retrieval  全段检索判定（dar_retrieval_check，增量：已判段复用；人工边界优先）
  l3         全段四类预标（dar_l3 --all，供标注工具注入；人工边界优先）
  tool       生成标注版工具（注入预标 + 人工边界）
  report     聚合周报：L1/L2 + 检索交叉 + 预标分布 + 人工标注进度 + 与上周对比，
             落盘 processed/weekly_YYYYMMDD.json

用法：
  python ai/scripts/dar_weekly.py export prepare l1 retrieval l3 tool report
  python ai/scripts/dar_weekly.py report                    # 只重出报告
  python ai/scripts/dar_weekly.py --env prod export         # 连生产导数据（目录隔离到 export_dar/prod/）
  python ai/scripts/dar_weekly.py --env prod --note 上线v2 prepare l1 ...   # 附注随周报落盘
环境：--env test（缺省）| prod。两环境数据/人工标注/周报完全隔离；
模型：l1/l3/retrieval 三步的判定用 DAR_MODEL（缺省 deepseek-v4.1-flash-expires-on-0910，
温度 0 无思考）；检索词改写走 pipeline 内部 get_intent_client（INTENT_MODEL）。

环境规定（用户 0910 定调，固定不再变）：
  1) 指标生成的数据从生产拿：对话记录走 --env prod（export 连生产库）；测试库数据
     只用于校准考卷/内部验证（--env test）。
  2) L1/L3/retrieval 等一切用到检索的步骤，去服务器的测试环境测（DAR_QDRANT 缺省
     test：隧道 + 测试服务指针）；本地快照又旧又慢，只作 DAR_QDRANT=local 应急。
  3) 在线测试也从测试环境测；生产库/生产服务只读，写操作绝不碰生产。
"""
import glob
import io
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime as _dt, timedelta as _td

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = r"C:/Users/PAJ26020/Desktop/export_dar"
SSH_HOST = "usp-a@125.122.97.107"
SSH_PORT = "8802"
TEST_PY = "~/miniconda3/envs/test-ai/bin/python"

# 环境档案：远程 .env（解析出目标库）/ 远程 python / 服务器临时目录。
# prod 无独立脚本环境时 fallback 用 test-ai python 读生产 .env（跨 env 读连接串无碍）。
ENVS = {
    "test": {"env": "/data/apps/TestOpenRobotService/ai/.env",
             "py": TEST_PY, "tmp": "/tmp/dar_export"},
    "prod": {"env": "/data/apps/OpenRobotService/ai/.env",
             "py": "/data/workspace/ai/bin/python", "tmp": "/tmp/dar_export_prod"},
}

# 当前环境（main 里按 --env 赋值；模块级缺省 test 保证直接 import 不炸）
ENV = "test"
DATA = os.path.join(DATA_ROOT, "test")
OUT = os.path.join(DATA, "processed")
# 人工切分/标注：随数据集放 export_dar/{env}/manual_segmentation.json
# （0910-6 迁出 Downloads——下载列表清理会误删；工具「保存到工作台」直写这里）
MANUAL = os.path.join(DATA, "manual_segmentation.json")
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
os.makedirs({tmpdir!r}, exist_ok=True)
for name, cols in {tables!r}.items():
    with conn.cursor() as cur, gzip.open(os.path.join({tmpdir!r}, name + ".csv.gz"),
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
'''.format(env="{ENV_FILE}", tmpdir="{TMP_DIR}", tables=EXPORT_TABLES)


def sh(cmd, **kw):
    print(f"$ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}")
    return subprocess.run(cmd, check=True, **kw)


def _git_out(*args):
    # encoding 必须 utf-8：git log 中文 message 在 GBK 控制台下解码崩 readerthread
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(HERE))
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def _write_export_meta():
    """版本锚点：export 时记 git HEAD 与近期 test 合入，周报据此呈现「本周部署」。
    meta.json 存数组（append），report 取最近两条做差异。"""
    head = _git_out("rev-parse", "--short", "HEAD")
    since = ""
    metas = []
    mp = os.path.join(DATA, "meta.json")
    if os.path.exists(mp):
        try:
            metas = json.load(open(mp, encoding="utf-8"))
            last = metas[-1].get("at", "")[:10]
            if last:
                since = f'--since="{last}"'
        except Exception:
            metas = []
    merges = [l for l in _git_out("log", "upstream/test", "--oneline",
                                  "--merges", "-15",
                                  *([f"--since={last}"] if since else [])
                                  ).splitlines() if l]
    metas.append({"at": _dt.now().isoformat(timespec="seconds"),
                  "env": ENV, "head": head, "merges": merges})
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(metas, fh, ensure_ascii=False, indent=1)
    print(f"版本锚点: {head}，近期合入 {len(merges)} 条 → {mp}")


def step_export():
    os.makedirs(DATA, exist_ok=True)  # 首跑（如 prod）目录可能不存在，scp 目标要先建
    cfg = ENVS[ENV]
    py, envf, tmpdir = cfg["py"], cfg["env"], cfg["tmp"]
    # prod 脚本环境可能没装 pymysql：探测失败换 test-ai python 跑导出
    # （python 只是执行器，连接串永远来自目标环境自己的 .env，跨 env 执行无碍）
    if ENV == "prod":
        probe = subprocess.run(["ssh", "-p", SSH_PORT, SSH_HOST,
                                f"{py} -c 'import pymysql'"],
                               capture_output=True, text=True)
        if probe.returncode != 0:
            print(f"[export] {py} 缺 pymysql，改用 {TEST_PY} 执行（连接串仍读生产 .env）")
            py = TEST_PY
    script = (REMOTE_EXPORT
              .replace("{ENV_FILE}", envf)
              .replace("{TMP_DIR}", tmpdir))
    remote_cmd = f"{py} - <<'DARPYEOF'\n{script}\nDARPYEOF"
    sh(["ssh", "-p", SSH_PORT, SSH_HOST, remote_cmd])
    sh(["scp", "-P", SSH_PORT, f"{SSH_HOST}:{tmpdir}/*.csv.gz", DATA + "/"])
    _write_export_meta()
    print(f"导出落位 {DATA}/（四表 csv.gz）")


def step(name, script, args=()):
    print(f"\n{'=' * 72}\n== {name}：{script} {' '.join(args)}\n{'=' * 72}")
    sh([sys.executable, os.path.join(HERE, script), *args])


def step_l1_replay():
    """L1 重算：--replay --review 吸收人工切分/标签（读已落盘判定，不调 LLM）。
    无人工文件/无对话数据时跳过；classified 落后于新导出时回退 l1 全量重判（带 --review）。"""
    if not os.path.exists(MANUAL):
        print("l1r：无人工切分/标注文件，跳过（L1 维持 LLM 切分口径）")
        return
    cls = os.path.join(OUT, "conversations_classified.jsonl")
    fresh = (os.path.exists(cls) and os.path.exists(SPLIT)
             and os.path.getmtime(cls) >= os.path.getmtime(SPLIT))
    if not os.path.exists(SPLIT):
        print("l1r：无对话数据（先跑 export/prepare），跳过")
        return
    if fresh:
        step("l1 重算（吸收人工切分）", "dar_l1.py",
             ("--out", OUT, "--replay", "--review", MANUAL))
    else:
        STEPS["l1"]()


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


def _load_csv_rows():
    """最新 review CSV → 段级底表（type/user 维度下钻与解决轮次数据源）。"""
    import csv as _csv
    files = sorted(glob.glob(os.path.join(OUT, "direct_answer_review_*.csv")))
    if not files:
        return [], ""
    with open(files[-1], encoding="utf-8-sig") as fh:
        return list(_csv.DictReader(fh)), os.path.basename(files[-1])


def _drilldown_matrix(csv_rows):
    """真实组 user × 话题类型 → L1 段级直答率（未出单段/段数）。行=人，列=type。"""
    if not csv_rows or not os.path.exists(SPLIT):
        return None
    uname = {str(c["conversation_id"]): (c.get("name") or c.get("user_id") or "?")
             for c in (json.loads(l) for l in open(SPLIT, encoding="utf-8"))}
    cell = {}
    for r in csv_rows:
        if r["group"] != "真实组":
            continue
        u = uname.get(r["conversation_id"], "?")
        ok = not int(r["seg_ticketed"])
        c = cell.setdefault(u, {}).setdefault(r.get("type") or "未分类", [0, 0])
        c[0] += ok
        c[1] += 1
    if not cell:
        return None
    types = sorted({t for m in cell.values() for t in m})
    def row_rate(m):
        ok = sum(v[0] for v in m.values())
        n = sum(v[1] for v in m.values())
        return ok / n if n else 0
    users = sorted(cell, key=lambda u: (row_rate(cell[u]), -sum(v[1] for v in cell[u].values())))
    return {"types": types, "users": [
        {"user": u, "rate": row_rate(cell[u]),
         "cells": {t: (f"{cell[u][t][0]}/{cell[u][t][1]}" if t in cell[u] else "")
                   for t in types},
         "total": sum(v[1] for v in cell[u].values())} for u in users]}


def _fail_items(j_rows):
    """未直答/未覆盖段全量清单（不截断），供工作台按周浏览。
    **人工标签优先于 AI 预标**（0910 实锤：用户标了「直接提单」的段仍进清单——
    原先只看 pre，人工纠正被无视）。段上有人工标签（含预填）按标签计，
    无标签段才按 AI 预标计。
    每条含问题/type/判定/理由/用户/时间/所在周（周一日期）——
    周增量=按 week 分组后的各组条数。
    纯问候/寒暄段（段内无 L1 咨询回合，如整段只有「你好」）不进清单；
    问候开头但段内有真问题的，展示首个咨询回合的问题而非段首「你好」。"""
    if not j_rows or not os.path.exists(SPLIT) or not os.path.exists(
            os.path.join(OUT, "conversations_classified.jsonl")):
        return []
    labs_man = {}
    if os.path.exists(MANUAL):
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        for cid, lm in (json.load(open(MANUAL, encoding="utf-8"))
                        .get("labels") or {}).items():
            labs_man[str(cid)] = {int(k): legacy.get(v, v) for k, v in lm.items()
                                  if str(k).isdigit()}
    bad = []
    for r in j_rows:
        if r.get("grp") != "真实组":
            continue  # 测试组是自测流量，不进缺口清单
        eff = labs_man.get(str(r["cid"]), {}).get(r.get("astart")) or r.get("pre")
        if eff in ("未直答", "未覆盖"):
            bad.append((r, eff))
    if not bad:
        return []
    convs = {str(c["conversation_id"]): c for c in
             (json.loads(l) for l in open(SPLIT, encoding="utf-8"))}
    cls_by = {str(j["conversation_id"]): j["cls"] for j in
              (json.loads(l) for l in open(
                  os.path.join(OUT, "conversations_classified.jsonl"),
                  encoding="utf-8"))}
    starts = {}  # cid → 段起点集合（j_rows 是全段判定，含未失败段，边界才完整）
    for r in j_rows:
        if r.get("astart") is not None:
            starts.setdefault(str(r["cid"]), set()).add(r["astart"])
    items = []
    for r, eff in bad:
        c, cl = convs.get(str(r["cid"])), cls_by.get(str(r["cid"]))
        a = r.get("astart")
        if not c or not cl or a is None or a >= len(c["rounds"]) or a >= len(cl):
            continue
        ast = sorted(starts.get(str(r["cid"])) or [])
        e = next((x for x in ast if x > a), len(c["rounds"]))
        qi = next((i for i in range(a, min(e, len(cl)))
                   if cl[i].get("q") and (c["rounds"][i].get("q") or "").strip()),
                  None)
        if qi is None:  # 段内无咨询回合=纯问候/寒暄，不是可改进的失败问题
            continue
        q = (c["rounds"][qi]["q"] or "").strip()[:80]
        if not q:
            continue
        at = (c["rounds"][qi].get("at") or "")[:16]
        t = _dt.fromisoformat(at) if at else None
        week = (t - _td(days=t.weekday())).strftime("%Y-%m-%d") if t else ""
        items.append({"pre": eff, "q": q, "type": cl[qi].get("type") or "未分类",
                      "reason": (r.get("reason") or "")[:60],
                      "user": (c.get("name") or c.get("user_id") or "?"),
                      "cid": r["cid"], "astart": a, "at": at, "week": week})
    return items


def _fail_list(items):
    """全量清单 → 按 type 分组（周报 md 用，>30 段截断标注）。"""
    if not items:
        return None
    groups = {}
    for it in items:
        groups.setdefault(it["type"], []).append(
            {k: it[k] for k in ("pre", "q", "reason", "cid", "astart")})
    out = []
    for t in sorted(groups, key=lambda k: -len(groups[k])):
        g = groups[t]
        out.append({"type": t, "n": len(g), "truncated": len(g) > 30, "items": g[:30]})
    return out


def _this_monday():
    d = _dt.now()
    return (d - _td(days=d.weekday())).strftime("%Y-%m-%d")


def _this_week_block(csv_rows):
    """本周新增对话的指标（真实组）：窗口=上次导出锚点→本次导出锚点，
    只有一条锚点时回退最近 7 天。全量口径随数据累积慢变，本周口径回答
    「这周服务质量怎么样」。L2 人工标注通常滞后，本周只算 L1 段级。"""
    if not csv_rows:
        return None
    to_at = from_at = None
    mp = os.path.join(DATA, "meta.json")
    try:
        metas = json.load(open(mp, encoding="utf-8"))
        to_at = _dt.fromisoformat((metas[-1].get("at") or "")[:19])
        from_at = (_dt.fromisoformat((metas[-2].get("at") or "")[:19])
                   if len(metas) >= 2 else to_at - _td(days=7))
    except Exception:
        to_at = _dt.now()
        from_at = to_at - _td(days=7)
    rows = []
    for r in csv_rows:
        if r.get("group") != "真实组":
            continue
        try:
            t = _dt.fromisoformat((r.get("time") or "")[:19].replace("T", " "))
        except ValueError:
            continue
        if from_at <= t < to_at:
            rows.append(r)
    if not rows:
        return {"from": from_at.strftime("%m-%d"), "to": to_at.strftime("%m-%d"),
                "n_segs": 0, "note": "窗口内无新对话"}
    n, tk = len(rows), sum(int(r["seg_ticketed"]) for r in rows)
    return {"from": from_at.strftime("%m-%d"), "to": to_at.strftime("%m-%d"),
            "n_convs": len({r["conversation_id"] for r in rows}),
            "n_segs": n, "n_ticketed": tk,
            "L1_rate": f"{(n - tk) / n * 100:.1f}%（{n - tk}/{n}）"}


def _pct_val(s):
    """「76.6%（23/31）」→ 76.6，供环比数值化。"""
    try:
        return float(_re_search_pct(s))
    except (TypeError, ValueError):
        return None


def _re_search_pct(s):
    import re as _re2
    m = _re2.search(r"([\d.]+)%", str(s or ""))
    return m.group(1) if m else None


def _wow_block(prev_rep, rep):
    """整体直答率环比（同分母三口径，数值化对比上次周报）。
    分母=人工已标段，新增标注会让两期数字都动，delta 注明口径相同才可比。"""
    keys = ("L1_段级", "L2_人工", "L3_AI同段")
    prev, curr = (prev_rep or {}).get("dar_rates_same_base") or {}, \
        (rep or {}).get("dar_rates_same_base") or {}
    wow = {}
    for k in keys:
        p, c = _pct_val(prev.get(k)), _pct_val(curr.get(k))
        if p is None or c is None:
            continue
        wow[k] = {"prev": p, "curr": c, "delta": round(c - p, 1)}
    return wow or None


def _precision_by_label(j_rows):
    """四类预标 precision（人工已标段为基准）：预标X且人工X / 预标X。
    高 precision=预标说是什么就是什么（可放权）；低=预标滥标。"""
    labs = ["直接提单", "直答正确", "未直答", "未覆盖"]
    hit, tot = Counter(), Counter()
    for r in j_rows:
        if r.get("lab") in labs and r.get("pre") in labs:
            tot[r["pre"]] += 1
            hit[r["pre"]] += r["pre"] == r["lab"]
    n_hit, n_tot = sum(hit.values()), sum(tot.values())
    if not n_tot:
        return None
    return {"overall": f"{n_hit}/{n_tot} = {n_hit / n_tot * 100:.0f}%",
            "by_label": {l: (f"{hit[l]}/{tot[l]}" if tot[l] else "—") for l in labs},
            "delegable": n_hit / n_tot >= 0.9}


def _meta_block():
    """版本锚点（meta.json 最近一条）+ --note 附注 + 本周合入。"""
    mp = os.path.join(DATA, "meta.json")
    if not os.path.exists(mp):
        return {"note": NOTE} if NOTE else None
    metas = []
    try:
        metas = json.load(open(mp, encoding="utf-8"))
    except Exception:
        pass
    blk = {"note": NOTE} if NOTE else {}
    if metas:
        last = metas[-1]
        blk.update({"export_at": last.get("at", "")[:16], "git_head": last.get("head", ""),
                    "merges": last.get("merges") or []})
    return blk or None


def _md_table(header, rows):
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _write_md(rep, path):
    """Markdown 周报：可直接发领导的形态（json 保留全部细节）。"""
    L = [f"# 直答率周报 {rep['date']}（{ENV} 环境）", ""]
    if rep.get("note"):
        L += [f"> 附注：{rep['note']}", ""]
    if rep.get("meta"):
        m = rep["meta"]
        L += [f"**版本**：git `{m.get('git_head','')}`（导出于 {m.get('export_at','')}）"]
        for mg in (m.get("merges") or [])[:10]:
            L.append(f"- {mg}")
        L.append("")
    if rep.get("dar_rates"):
        L += ["## 三口径直答率（真实组）", "",
              _md_table(["口径", "数值"],
                        [[k, v] for k, v in rep["dar_rates"].items()]), ""]
        if rep.get("l1_note"):
            L += [f"*{rep['l1_note']}*", ""]
    if rep.get("dar_rates_same_base"):
        s = rep["dar_rates_same_base"]
        L += [f"## 同分母对比（{s['base']}，消除各口径剔除规则差异）", "",
              _md_table(["口径", "数值"],
                        [[k, v] for k, v in s.items() if k != "base"]), ""]
    if rep.get("wow"):
        L += ["## 整体直答率环比（对比上次周报，同分母口径）", "",
              _md_table(["口径", "上期", "本期", "变化"],
                        [[k, f"{v['prev']}%", f"{v['curr']}%",
                          f"{v['delta']:+.1f}pp"]
                         for k, v in rep["wow"].items()]),
              "*分母=人工已标段，两期新增标注都会让数字动，方向比绝对值重要*", ""]
    if rep.get("this_week"):
        t = rep["this_week"]
        L += ["## 本周新增对话（" + f"{t['from']}~{t['to']}" + "）", ""]
        if t.get("n_segs"):
            L += [f"- 会话 {t['n_convs']} 场｜话题段 {t['n_segs']}（出单 {t.get('n_ticketed', 0)}）",
                  f"- L1 段级直答率 {t['L1_rate']}（人工标注滞后，本周只算 L1）"]
        else:
            L += [f"- {t.get('note', '窗口内无新对话')}"]
        L.append("")
    if rep.get("l1_真实组"):
        L += ["## L1 明细（最新月）", "",
              _md_table(["指标", "值"],
                        [[k, v] for k, v in rep["l1_真实组"].items()]), ""]
    if rep.get("kb_gap") is not None:
        L += [f"## KB 缺口率：{rep['kb_gap']}",
              "（真实组检索重放 no 占比：资料层上界，含直接提单段；"
              "补库可救的实际缺口看 L3 未覆盖）", ""]
    if rep.get("matrix"):
        mx = rep["matrix"]
        L += ["## 下钻矩阵：用户 × 话题类型（L1 段级直答率=未出单段/段数）", "",
              _md_table(["用户", "合计段数", "整体", *mx["types"]],
                        [[u["user"], u["total"], f"{u['rate']*100:.0f}%",
                          *[u["cells"].get(t, "") for t in mx["types"]]]
                         for u in mx["users"]]), ""]
    if rep.get("avg_rounds"):
        L += ["## 平均解决轮次（真实组，段内回合数）", "",
              "｜".join(f"{k} {v}" for k, v in rep["avg_rounds"].items()), ""]
    if rep.get("ticket_quality"):
        L += ["## 转单质量（真实组）", "",
              "｜".join(f"{k} {v}" for k, v in rep["ticket_quality"].items()), ""]
    if rep.get("precision"):
        p = rep["precision"]
        L += [f"## L3 预标质量（人工已标段为基准）", "",
              f"总体 precision {p['overall']}" + ("，≥90% 可放权" if p["delegable"] else ""),
              "", _md_table(["预标类", "precision(对/预标数)"],
                            [[l, v] for l, v in p["by_label"].items()]), ""]
    if rep.get("cal_trend"):
        t = rep["cal_trend"]
        L += [f"**滚动校准**：上期（{t['prev']}）{t['prev_rate']} → 本期 {t['curr_rate']}", ""]
    if rep.get("fails"):
        L += ["## 失败清单（L3 预标未直答/未覆盖，按话题类型分组=知识缺口）", ""]
        for g in rep["fails"]:
            more = f"（共 {g['n']} 条，仅列 30）" if g["truncated"] else ""
            L.append(f"### {g['type']} {g['n']} 段{more}")
            for it in g["items"]:
                L.append(f"- [{it['pre']}] {it['q']}｜{it['reason']}")
            L.append("")
        if rep.get("unanswered_total"):
            L += [f"*全量 {rep['unanswered_total']} 条（含用户/时间/所在周）已落 "
                  "`unanswered_*.json`，工作台「未直答清单」可按周增量浏览*", ""]
    if rep.get("delta"):
        L += ["## 环比（对比上次周报）", ""]
        for d in rep["delta"]:
            L.append(f"- {d}")
        L.append("")
    L.append(f"*数据源：{rep.get('l1_source','')}｜{rep.get('retrieval_source','')}｜"
             f"{rep.get('pre_source','')}｜{rep.get('cal_source','')}*")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def _overlay_manual_labels(rows):
    """人工标签覆盖进判定行（lab ← MANUAL 的标签，无标签行保持原值）。

    0910 实锤：判定行是增量复用的，行内 lab 停在判定时刻——用户其后改的
    标签（含直接提单改判）永远进不了周报；precision/一致率/未直答清单
    全部失真。凡吃 j_rows 的统计必须先过这一层。"""
    if not os.path.exists(MANUAL):
        return rows
    legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
    labs_man = {}
    for cid, lm in (json.load(open(MANUAL, encoding="utf-8"))
                    .get("labels") or {}).items():
        labs_man[str(cid)] = {int(k): legacy.get(v, v) for k, v in lm.items()
                              if str(k).isdigit()}
    for r in rows:
        lab = labs_man.get(str(r.get("cid")), {}).get(r.get("astart"))
        if lab:
            r["lab"] = lab
    return rows


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
        rows = _overlay_manual_labels(
            json.load(open(j_path, encoding="utf-8")))
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
    # 只认规范名 l3_judge_YYYYMMDD.json：_vN 是历史轮次归档，按名排序会排在规范名之后，
    # 取 [-1] 会取到被弃的那一轮（0910 实锤：r4 试跑归档后成了「最新」）
    cal_files = [f for f in sorted(glob.glob(os.path.join(OUT, "l3_judge_[0-9]*.json")))
                 if os.path.basename(f)[len("l3_judge_"):-len(".json")].isdigit()]
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
                # 忠实性前置：编造回答（faithful=no）不给直答分，归未直答
                if r.get("faithful") == "no":
                    return "未直答"
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
        rep["cal_source"] = os.path.basename(cal_path)
        # 滚动校准：与上一期校准文件比三分类对齐率（判断 judge 质量走向）
        def _tri_rate(rs):
            hit = tot = 0
            for r in rs:
                if r.get("lab") not in labs:
                    continue
                solved = r["intent"] == "consult" and r["resolved"] == "yes" \
                    and r.get("faithful") != "no"
                want = ("直接提单" if r["lab"] == "直接提单"
                        else "直答正确" if r["lab"] == "直答正确" else "未解决")
                got = ("直接提单" if r["intent"] == "ticket"
                       else "直答正确" if solved else "未解决")
                tot += 1
                hit += want == got
            return f"{hit}/{tot} = {hit / tot * 100:.1f}%" if tot else ""
        if len(cal_files) >= 2:
            rep["cal_trend"] = {
                "prev": os.path.basename(cal_files[-2]), "prev_rate": _tri_rate(
                    json.load(open(cal_files[-2], encoding="utf-8"))),
                "curr_rate": _tri_rate(cal)}
            print(f"滚动校准：上期 {rep['cal_trend']['prev_rate']} → "
                  f"本期 {rep['cal_trend']['curr_rate']}")
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
            f"端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
            f"｜确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
            f"｜已标 {ok + bad + unc} 段")
    if j_path:
        sub = [r for r in rows if r.get("grp") == "真实组"]
        p = Counter(r.get("pre", "?") for r in sub)
        ok, bad, unc = p.get("直答正确", 0), p.get("未直答", 0), p.get("未覆盖", 0)
        if ok + bad:
            rates["L3_AI预标"] = (
                f"端到端 {ok / (ok + bad + unc) * 100:.1f}%（{ok}/{ok + bad + unc}）"
                f"｜确定 {ok / (ok + bad) * 100:.1f}%（{ok}/{ok + bad}）"
                f"｜全段 {len(sub)}（judge 偏宽仅供参考）")
    if rates:
        rep["dar_rates"] = rates
        # 分母纯化口径：L1 只计有实质咨询回合的话题段（纯提单/纯问候段不进分母）
        rep["l1_note"] = "分母=有实质咨询回合的话题段（segs_q，段级）；纯提单/纯问候段已剔除"
        print("\n== 直答率三口径对比（真实组；L1 分母已剔除非咨询会话）==")
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

    # ---- 六项指标增强：KB 缺口 / 下钻矩阵 / 解决轮次 / 转单质量 / 预标 precision / 失败清单 ----
    kb = (rep.get("retrieval_no_rate") or {}).get("真实组")
    if kb is not None:
        rep["kb_gap"] = (f"{kb * 100:.1f}%（真实组检索重放 no 占比，资料层上界；"
                         "含直接提单段，≠L3 未覆盖）")
        print(f"\nKB 缺口率：{rep['kb_gap']}")
    csv_rows, csv_name = _load_csv_rows()
    if csv_rows:
        rep["csv_source"] = csv_name
        mx = _drilldown_matrix(csv_rows)
        if mx:
            rep["matrix"] = mx
            print(f"下钻矩阵：{len(mx['users'])} 用户 × {len(mx['types'])} 类型")
        real = [r for r in csv_rows if r["group"] == "真实组"]
        ok_r = [int(r["n_rounds"]) for r in real if r["l2_label"] == "直答正确"]
        all_r = [int(r["n_rounds"]) for r in real]
        tk_r = [int(r["n_rounds"]) for r in real if r["seg_ticketed"] == "1"]
        if ok_r and all_r:
            rep["avg_rounds"] = {
                "直答正确段": f"{sum(ok_r) / len(ok_r):.1f} 轮（{len(ok_r)} 段）",
                "全部段": f"{sum(all_r) / len(all_r):.1f} 轮（{len(all_r)} 段）"}
            if tk_r:
                rep["avg_rounds"]["转工单段"] = (
                    f"{sum(tk_r) / len(tk_r):.1f} 轮（{len(tk_r)} 段）")
            print(f"平均解决轮次：{rep['avg_rounds']}")
        tk_rows = [r for r in real if r["seg_ticketed"] == "1"]
        if tk_rows:
            tk_lab = Counter(r["l2_label"] or "未标" for r in tk_rows)
            sug = sum(1 for r in real if r["suggest_no_ticket"] == "1")
            rep["ticket_quality"] = {
                "出单段L2构成": dict(tk_lab.most_common()),
                "建议转单未提单": f"{sug} 段（AI 建议了但用户没提）"}
            print(f"转单质量：{rep['ticket_quality']}")
    if j_path:
        prec = _precision_by_label(rows)
        if prec:
            rep["precision"] = prec
            print(f"L3 预标 precision：{prec['overall']}"
                  + ("（≥90% 可放权）" if prec["delegable"] else ""))
        items = _fail_items(rows)
        if items:
            ua_path = os.path.join(OUT, f"unanswered_{_dt.now():%Y%m%d}.json")
            weeks = Counter(i["week"] for i in items if i["week"])
            with open(ua_path, "w", encoding="utf-8") as fh:
                json.dump({"generated": f"{_dt.now():%Y-%m-%d %H:%M}",
                           "total": len(items),
                           "weeks": {w: n for w, n in sorted(weeks.items())},
                           "items": items}, fh, ensure_ascii=False, indent=1)
            rep["unanswered_total"] = len(items)
            rep["unanswered_this_week"] = weeks.get(_this_monday(), 0)
            print(f"未直答清单：{len(items)} 条（本周 +{rep['unanswered_this_week']}"
                  f"）→ {os.path.basename(ua_path)}")
        fl = _fail_list(items)
        if fl:
            rep["fails"] = fl
            print(f"失败清单：{sum(g['n'] for g in fl)} 段"
                  f"（{len(fl)} 类，type 即知识缺口聚类维度）")
    mb = _meta_block()
    if mb:
        rep["meta"] = mb
        if mb.get("git_head"):
            print(f"版本锚点：{mb['git_head']}，本周合入 {len(mb.get('merges') or [])} 条")
        if mb.get("note"):
            print(f"附注：{mb['note']}")

    prevs = [p for p in sorted(glob.glob(os.path.join(OUT, "weekly_*.json")))
             if os.path.basename(p) != f"weekly_{_dt.now():%Y%m%d}.json"]
    prev_rep = None
    if prevs:
        prev_rep = json.load(open(prevs[-1], encoding="utf-8"))
        delta = []
        for k in ("l1_真实组", "retrieval_no_rate", "manual_progress",
                  "kb_gap", "dar_rates_same_base"):
            old, new = prev_rep.get(k), rep.get(k)
            if old != new and (old is not None or new is not None):
                delta.append(f"{k}: {old} → {new}")
        if delta:
            rep["delta"] = delta
            print(f"对比 {os.path.basename(prevs[-1])} → 本周变化：")
            for d in delta:
                print(f"  {d}")
    wow = _wow_block(prev_rep, rep)
    if wow:
        rep["wow"] = wow
        print("整体直答率环比（同分母三口径）：")
        for k, v in wow.items():
            print(f"  {k}: {v['prev']}% → {v['curr']}%（{v['delta']:+.1f}pp）")
    tw = _this_week_block(csv_rows)
    if tw:
        rep["this_week"] = tw
        print(f"本周新增（{tw['from']}~{tw['to']}）："
              + (f"{tw['n_convs']} 会话 {tw['n_segs']} 段，"
                 f"L1 {tw['L1_rate']}" if tw.get("n_segs") else tw.get("note", "")))

    path = os.path.join(OUT, f"weekly_{_dt.now():%Y%m%d}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    md_path = os.path.join(OUT, f"weekly_{_dt.now():%Y%m%d}.md")
    _write_md(rep, md_path)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    print(f"周报: {path}")
    print(f"Markdown: {md_path}")


STEPS = {
    "export": lambda: step_export(),
    "prepare": lambda: step("prepare", "dar_prepare.py"),
    # dar_l1 --out 默认 Desktop（非 processed），显式传；有人工标注带上 --review 出 L2
    "l1": lambda: step("l1", "dar_l1.py", ("--out", OUT) +
                       (("--review", MANUAL) if os.path.exists(MANUAL) else ())),
    "tool0": lambda: step("tool 切题（无预标）", "build_segmentation_tool.py",
                          ("--bounds-only",)),
    "l1r": lambda: step_l1_replay(),
    "retrieval": lambda: step("retrieval", "dar_retrieval_check.py"),
    "l3": lambda: step("l3 预标", "dar_l3.py", ("--all",)),
    "tool": lambda: step("tool", "build_segmentation_tool.py"),
    "report": lambda: step_report(),
}


NOTE = ""  # --note 附注，随周报落盘


def _migrate_legacy():
    """一次性迁移：老版本四表 csv.gz 与 processed/ 直接放 DATA_ROOT 根下，
    首次以 test 环境跑时挪进 test/（prod 不迁，防把 test 老数据误归 prod）。"""
    files = glob.glob(os.path.join(DATA_ROOT, "*.csv.gz"))
    dirs = [d for d in ("processed",) if os.path.isdir(os.path.join(DATA_ROOT, d))]
    if not files and not dirs:
        return
    os.makedirs(DATA, exist_ok=True)
    for p in files:
        os.rename(p, os.path.join(DATA, os.path.basename(p)))
    for d in dirs:
        os.rename(os.path.join(DATA_ROOT, d), os.path.join(DATA, d))
    print(f"老数据迁移：{len(files)} 个 csv.gz + processed/ → {DATA}/")


def main():
    global ENV, DATA, OUT, MANUAL, SPLIT, NOTE
    args, names = sys.argv[1:], []
    env = os.environ.get("DAR_ENV", "test")  # 缺省跟随环境变量（与子脚本口径一致）
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--env" and i + 1 < len(args):
            i += 1
            env = args[i]
        elif a.startswith("--env="):
            env = a.split("=", 1)[1]
        elif a == "--note" and i + 1 < len(args):
            i += 1
            NOTE = args[i]
        elif a.startswith("--note="):
            NOTE = a.split("=", 1)[1]
        else:
            names.append(a)
        i += 1
    if env not in ENVS:
        sys.exit(f"未知环境 {env!r}；可用：{list(ENVS)}")
    ENV = env
    os.environ["DAR_ENV"] = env  # 子脚本按环境变量取数据目录（subprocess 继承）
    DATA = os.path.join(DATA_ROOT, ENV)
    OUT = os.path.join(DATA, "processed")
    MANUAL = os.path.join(DATA, "manual_segmentation.json")
    SPLIT = os.path.join(OUT, "conversations_split.jsonl")
    # 检索源（规定，用户 0910 定调）：L1/L3/retrieval 一律走服务器测试环境；
    # 本地快照又旧又慢，仅 DAR_QDRANT=local 应急。数据由 --env 决定，与检索源无关。
    os.environ.setdefault("DAR_QDRANT", "test")
    if ENV == "test":
        _migrate_legacy()
    if not names:
        names = [n for n in STEPS if n != "export"]  # 缺省本地全流程
    bad = [n for n in names if n not in STEPS]
    if bad:
        sys.exit(f"未知步骤 {bad}；可用：{list(STEPS)}")
    for n in names:
        STEPS[n]()


if __name__ == "__main__":
    main()
