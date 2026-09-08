# -*- coding: utf-8 -*-
"""生成话题切分审核工具（单 HTML，浏览器直接打开）。

输入：processed/conversations_split.jsonl + conversations_classified.jsonl
     + l3_judge_all_*.json（AI 预标，可选） + retrieval_check_*.json（检索判定，可选）
输出：processed/segmentation_tool.html（数据内嵌，无依赖离线可用）

交互：橙色虚线=话题边界（LLM 预切），可拖动/增删；审完「保存并下一个」；
导出 manual_segmentation.json 后用 dar_l1.py --replay --review 重算。
进度存 localStorage（换浏览器/清缓存会丢，审完及时导出）。

预标模式：注入 AI 四类预标（直接提单/直答正确/未直答/未覆盖）+ 检索判定 +
judge 理由，段头一键采纳；数字键 1-6 选标签。预标按 AI 段首索引锚定，
边界改动后该段预标不显示（防错位）。
"""
import glob
import io
import json
import os
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = r"C:/Users/PAJ26020/Desktop/export_dar/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
TPL = os.path.join(HERE, "segmentation_tool.template.html")

WINDOW = timedelta(minutes=30)
A_MAX = 3000  # AI 回答注入上限（审核看话题够用）


def latest(pattern):
    files = sorted(glob.glob(os.path.join(OUT, pattern)))
    return files[-1] if files else ""


def load_pre():
    """AI 预标 {cid: {ai段首索引: {pre, rv, reason}}}。段首按 AI topic 首现索引。"""
    j_path, r_path = latest("l3_judge_all_*.json"), latest("retrieval_check_*.json")
    if not j_path:
        return {}
    j_rows = json.load(open(j_path, encoding="utf-8"))
    rv = {}
    if r_path:
        for r in json.load(open(r_path, encoding="utf-8")):
            rv[(str(r["cid"]), r.get("astart", r.get("seg")))] = r.get("verdict")
    pre = {}
    for r in j_rows:
        astart = r.get("astart")
        if astart is None:
            continue
        pre.setdefault(str(r["cid"]), {})[astart] = {
            "pre": r.get("pre", ""), "rv": rv.get((str(r["cid"]), astart), ""),
            "reason": (r.get("reason") or "")[:80],
        }
    print(f"AI 预标注入：{j_path}（{len(pre)} 会话）" + (f"｜检索 {r_path}" if r_path else "（无检索判定）"))
    return pre


def ts(s):
    try:
        return datetime.fromisoformat((s or "").split(".")[0]).timestamp() * 1000
    except ValueError:
        return 0


def main():
    convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
    cls_all = {j["conversation_id"]: j["cls"] for j in
               (json.loads(l) for l in open(CLS, encoding="utf-8"))}
    pre = load_pre()

    out = []
    for c in convs:
        rounds = c["rounds"]
        if len(rounds) < 2:
            continue  # 单回合无切分余地
        cid = str(c["conversation_id"])
        cls = cls_all.get(cid)
        if not cls or len(cls) != len(rounds):
            cls = [{"q": True, "t": False, "topic": 0} for _ in rounds]
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
    payload = json.dumps({"convs": out}, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__DATA__", payload)
    path = os.path.join(OUT, "segmentation_tool.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"生成 {path}")
    print(f"会话 {len(out)} 个（≥2 回合），回合 {sum(len(c['rounds']) for c in out)}")
    n_multi = sum(1 for c in out
                  if len({r["t"] for r in c["rounds"]}) >= 2)
    print(f"LLM 切出多话题的 {n_multi} 个")


if __name__ == "__main__":
    main()
