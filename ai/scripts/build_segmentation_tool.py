# -*- coding: utf-8 -*-
"""生成话题切分审核工具（单 HTML，浏览器直接打开）。

输入：processed/conversations_split.jsonl + conversations_classified.jsonl
     + Downloads/manual_segmentation*.json（人工边界，可选）
     + l3_judge_all_*.json（AI 预标，可选） + retrieval_check_*.json（检索判定，可选）
输出：processed/segmentation_tool.html（数据内嵌，无依赖离线可用）

两轮用法（0910 起：先人工定边界、后按人工边界判定——judge 与检索重放的
输入就是人工认可的话题段，标注轮预标全部有效）：
  --bounds-only  切题轮：不注入预标/检索，只核对修正话题边界，
                 导出 JSON 放 Downloads/（工作台第 3 步）
  （缺省）       标注轮：注入 AI 四类预标 + 检索判定 + judge 理由，段头一键采纳；
                 数字键 1-6 选标签（工作台第 4 步后）
已导出过人工边界的会话，初始切分即人工边界（与 localStorage 双重一致）。
预标按段首索引锚定，边界改动后该段预标不显示（防错位）。
进度存 localStorage（换浏览器/清缓存会丢，审完及时导出）。
"""
import argparse
import glob
import io
import json
import os
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.environ.get("DAR_ENV", "test")
OUT = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
TPL = os.path.join(HERE, "segmentation_tool.template.html")
MANUAL_NAME = {"test": "manual_segmentation.json",
               "prod": "manual_segmentation_prod.json"}
MANUAL = os.path.join(r"C:/Users/PAJ26020/Downloads", MANUAL_NAME[ENV])

WINDOW = timedelta(minutes=30)
A_MAX = 3000  # AI 回答注入上限（审核看话题够用）


def latest(pattern):
    files = sorted(glob.glob(os.path.join(OUT, pattern)))
    return files[-1] if files else ""


def load_pre():
    """AI 预标 + 检索 chunks {cid: {ai段首索引: {pre, rv, reason, chunks}}}。
    l3 预标缺席时仍注入 chunks-only 行（检索审核不依赖 l3）。"""
    j_path, r_path = latest("l3_judge_all_*.json"), latest("retrieval_check_*.json")
    j_rows = json.load(open(j_path, encoding="utf-8")) if j_path else []
    chk = {}
    if r_path:
        for r in json.load(open(r_path, encoding="utf-8")):
            chk[(str(r["cid"]), r.get("astart", r.get("seg")))] = {
                "rv": r.get("verdict", ""), "chunks": r.get("chunks") or []}
    pre = {}
    for r in j_rows:
        astart = r.get("astart")
        if astart is None:
            continue
        c = chk.get((str(r["cid"]), astart), {})
        pre.setdefault(str(r["cid"]), {})[astart] = {
            "pre": r.get("pre", ""), "rv": c.get("rv", ""),
            "reason": (r.get("reason") or "")[:80], "chunks": c.get("chunks", []),
        }
    for (cid, astart), c in chk.items():
        pre.setdefault(cid, {}).setdefault(
            astart, {"pre": "", "rv": c["rv"], "reason": "", "chunks": c["chunks"]})
    src = []
    if j_path:
        src.append(f"预标 {j_path}")
    if r_path:
        src.append(f"检索 {r_path}")
    print(f"注入：{'｜'.join(src) or '（无预标无检索判定）'}（{len(pre)} 会话）")
    return pre


def ts(s):
    try:
        return datetime.fromisoformat((s or "").split(".")[0]).timestamp() * 1000
    except ValueError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bounds-only", action="store_true",
                    help="切题轮：不注入 AI 预标/检索判定（边界定稿后再判定）")
    args = ap.parse_args()

    convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
    cls_all = {j["conversation_id"]: j["cls"] for j in
               (json.loads(l) for l in open(CLS, encoding="utf-8"))}
    man = {}
    if os.path.exists(MANUAL):
        man = json.load(open(MANUAL, encoding="utf-8")).get("bounds") or {}
    pre = {} if args.bounds_only else load_pre()
    if args.bounds_only:
        print("切题轮：不注入预标/检索（先人工定边界，判定在边界定稿后跑）")

    out = []
    n_man = n_llm_multi = n_noise = 0
    for c in convs:
        rounds = c["rounds"]
        if len(rounds) < 2:
            continue  # 单回合无切分余地
        cid = str(c["conversation_id"])
        cls = cls_all.get(cid)
        if not cls or len(cls) != len(rounds):
            cls = [{"q": True, "t": False, "topic": 0} for _ in rounds]
        # 全程无咨询且无工单（纯寒暄）：无话题可切、无标签可打，不进工具
        # （带工单的保留——纯提单会话是「直接提单」标的标的；指标层本就跳过无咨询段）
        if not any(k["q"] for k in cls) and not (c.get("tasks") or []):
            n_noise += 1
            continue
        if len({k.get("topic", 0) for k in cls}) >= 2:
            n_llm_multi += 1
        task_ts = [ts(t["at"]) for t in c.get("tasks") or []]
        rj = []
        for i, r in enumerate(rounds):
            rt = ts(r["at"])
            rj.append({
                "q": (r["q"] or "")[:500],
                "a": "\n".join(r["a"])[:A_MAX],
                "at": (r["at"] or "")[:16],
                "ts": rt,
                "cq": cls[i]["q"],
                "t": cls[i].get("topic", 0),
                "tk": any(rt <= tt <= rt + WINDOW.total_seconds() * 1000
                          for tt in task_ts),
            })
        # 人工边界覆盖初始切分：没导出过边界的会话仍按 LLM topic 展示
        b = man.get(cid)
        if b:
            n_man += 1
            starts = sorted({0, *(int(x) for x in b
                                  if 0 <= int(x) < len(rounds))})
            si = 0
            for i in range(len(rj)):
                if si + 1 < len(starts) and i >= starts[si + 1]:
                    si += 1
                rj[i]["t"] = si
        out.append({
            "conversation_id": cid,
            "name": c.get("name") or "",
            "created_at": (c["created_at"] or "")[:16],
            "tester": c["is_tester"],
            "tasks": [{"id": t["id"], "at": (t.get("at") or "")[:16], "ts": ts(t.get("at"))}
                      for t in c.get("tasks") or []],
            "rounds": rj,
            "pre": pre.get(cid) or {},
        })
    out.sort(key=lambda x: x["created_at"])

    tpl = open(TPL, encoding="utf-8").read()
    # </ 转义：JSON 内嵌 <script> 时，内容里出现 </script> 会提前截断脚本（JS 字符串里 \/ 合法）
    payload = json.dumps({"convs": out, "env": ENV,
                          "mode": "bounds" if args.bounds_only else "label"},
                         ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__DATA__", payload)
    path = os.path.join(OUT, "segmentation_tool.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"生成 {path}")
    print(f"会话 {len(out)} 个（≥2 回合），回合 {sum(len(c['rounds']) for c in out)}"
          + (f"，滤掉纯寒暄（无咨询无工单）{n_noise} 个" if n_noise else ""))
    if man:
        print(f"人工边界嵌入 {n_man} 个已切会话（初始切分=人工边界），LLM 切出多话题的 {n_llm_multi} 个")
    else:
        print(f"LLM 切出多话题的 {n_llm_multi} 个")


if __name__ == "__main__":
    main()
