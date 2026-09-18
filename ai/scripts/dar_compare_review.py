# -*- coding: utf-8 -*-
"""对比 AI 切分 vs 人工切分，导出未覆盖/未直答清单（知识库补充依据）。"""
import io
import json
import os
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = r"C:/Users/PAJ26020/Desktop/export_dar/processed"
SPLIT = os.path.join(OUT, "conversations_split.jsonl")
CLS = os.path.join(OUT, "conversations_classified.jsonl")
MANUAL = r"C:/Users/PAJ26020/Downloads/manual_segmentation.json"

convs = [json.loads(l) for l in open(SPLIT, encoding="utf-8")]
cls_all = {j["conversation_id"]: j["cls"] for j in
           (json.loads(l) for l in open(CLS, encoding="utf-8"))}
man = json.load(open(MANUAL, encoding="utf-8"))
bounds, labels = man.get("bounds") or {}, man.get("labels") or {}
legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}

# ---- 1. 切分对比：AI topic vs 人工 bounds ----
n_same_segs = n_diff_segs = n_same_bounds = n_moved = 0
diff_detail = []
for c in convs:
    cid = str(c["conversation_id"])
    if cid not in bounds:
        continue
    rounds = c["rounds"]
    cls = cls_all.get(cid)
    if not cls or len(cls) != len(rounds):
        continue
    # AI 切分段首（归一化后）
    ai_starts, seen = [], set()
    for i, k in enumerate(cls):
        t = k.get("topic", 0)
        if t not in seen:
            seen.add(t)
            ai_starts.append(i)
    # ai_starts 是"每种 topic 首次出现"，但 topic 顺序未必单调，重排成真实段序：
    order = {}
    seq = 0
    for k in cls:
        t = k.get("topic", 0)
        if t not in order:
            order[t] = seq
            seq += 1
    ai = []
    seen2 = set()
    for i, k in enumerate(cls):
        t = k.get("topic", 0)
        if t not in seen2:
            seen2.add(t)
            ai.append(i)
    manual = sorted({0, *(int(x) for x in bounds[cid] if 0 <= int(x) < len(rounds))})
    if len(ai) == len(manual):
        n_same_segs += 1
        if ai == manual:
            n_same_bounds += 1
        else:
            n_moved += 1
            diff_detail.append((cid, "边界挪动", len(ai), ai, manual,
                                c["rounds"][0]["at"][:10]))
    else:
        n_diff_segs += 1
        diff_detail.append((cid, "段数不同", f"AI {len(ai)} vs 人工 {len(manual)}",
                            ai, manual, c["rounds"][0]["at"][:10]))

print(f"== 切分对比（人工 bounds 覆盖 {len(bounds)} 会话） ==")
print(f"段数一致 {n_same_segs}（其中边界全同 {n_same_bounds}，挪动 {n_moved}）"
      f"｜段数不同 {n_diff_segs}")

# ---- 2. 标签 × 行为信号交叉 ----
rows = []
for c in convs:
    cid = str(c["conversation_id"])
    if cid not in bounds:
        continue
    rounds = c["rounds"]
    cls = cls_all.get(cid)
    if not cls or len(cls) != len(rounds):
        continue
    lab_map = {int(k): legacy.get(v, v) for k, v in
               (labels.get(cid) or {}).items() if str(k).isdigit()}
    manual = sorted({0, *(int(x) for x in bounds[cid] if 0 <= int(x) < len(rounds))})
    grp = "测试组" if c["is_tester"] else "真实组"
    for tid, s in enumerate(manual):
        e = manual[tid + 1] if tid + 1 < len(manual) else len(rounds)
        lab = lab_map.get(s, "未标")
        q_idx = [i for i in range(s, e) if cls[i]["q"]
                 and any(a.strip() for a in rounds[i]["a"])]
        if not q_idx:
            continue
        suggest = any(cls[i]["t"] for i in q_idx)
        rows.append({
            "cid": c["conversation_id"], "grp": grp, "seg": tid, "lab": lab,
            "time": rounds[q_idx[0]]["at"][:16],
            "q": (rounds[q_idx[0]]["q"] or "").strip()[:120],
            "suggest": suggest,
            "name": c.get("name") or "",
        })

cross = Counter((r["lab"], r["suggest"]) for r in rows)
print("\n== 标签 × AI 建议转单 交叉（段级） ==")
for lab in ("直答正确", "未直答", "未覆盖", "直接提单", "未标"):
    a, b = cross.get((lab, True), 0), cross.get((lab, False), 0)
    if a + b:
        print(f"  {lab:<5} 共 {a+b:>3}｜AI 建议转单 {a:>3}｜未建议 {b:>3}")

# ---- 3. 导出未覆盖 / 未直答清单 ----
path = os.path.join(OUT, "kb_gaps_20260908.md")
with open(path, "w", encoding="utf-8") as fh:
    fh.write("# 知识库缺口清单（0908 L2 人工标注：真实组）\n\n")
    fh.write("> 未覆盖 = 知识库没有该内容（补内容的依据）；"
             "未直答 = 有相关内容但没答到位（治检索/回答链路的依据）\n\n")
    for want, title in (("未覆盖", "## 一、未覆盖（优先补进知识库）"),
                        ("未直答", "## 二、未直答（已有内容但未答好，逐个看是检索问题还是答案质量）")):
        fh.write(f"\n{title}\n\n")
        rr = [r for r in rows if r["lab"] == want and r["grp"] == "真实组"]
        rr.sort(key=lambda r: r["time"])
        for r in rr:
            tag = "｜AI曾建议转单" if r["suggest"] else ""
            fh.write(f"- {r['time']}｜[{r['cid']}#{r['seg']}] {r['q']}{tag}\n")
        fh.write(f"\n（共 {len(rr)} 段）\n")
print(f"\n清单已写: {path}")
print("未覆盖(真实组):", sum(1 for r in rows if r['lab'] == '未覆盖' and r['grp'] == '真实组'),
      "｜未直答(真实组):", sum(1 for r in rows if r['lab'] == '未直答' and r['grp'] == '真实组'))

# 分歧明细落盘（供抽查）
with open(os.path.join(OUT, "seg_diff_detail.json"), "w", encoding="utf-8") as fh:
    json.dump(diff_detail, fh, ensure_ascii=False, indent=1)
print(f"切分歧义明细 {len(diff_detail)} 条 → seg_diff_detail.json")
