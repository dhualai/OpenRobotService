# -*- coding: utf-8 -*-
"""抽查 dump：分歧会话的切分现场 + 各标签段的原文，供独立审核。"""
import io
import json
import os
import random
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = r"C:/Users/PAJ26020/Desktop/export_dar/processed"
convs = {str(c["conversation_id"]): c for c in
         (json.loads(l) for l in open(os.path.join(OUT, "conversations_split.jsonl"), encoding="utf-8"))}
man = json.load(open(r"C:/Users/PAJ26020/Downloads/manual_segmentation.json", encoding="utf-8"))
labels, bounds = man.get("labels") or {}, man.get("bounds") or {}

random.seed(42)

def dump_conv(cid, note=""):
    c = convs[cid]
    b = sorted({0, *(int(x) for x in (bounds.get(str(cid)) or []) if 0 <= int(x) < len(c["rounds"]))})
    print(f"\n{'='*70}\n会话 {cid} {note}｜{c.get('name')}｜{c['created_at'][:16]}｜"
          f"{'测试组' if c['is_tester'] else '真实组'}｜{len(c['rounds'])} 回合｜人工边界 {b}")
    labs = labels.get(str(cid)) or {}
    for i, r in enumerate(c["rounds"]):
        mark = " ◀切分" if i in b else ""
        lab = f" 〔{labs[str(i)]}〕" if str(i) in labs else ""
        q = (r["q"] or "").replace("\n", " ")[:150]
        a = next((x for x in r["a"] if x.strip()), "")
        print(f"#{i:2d}{mark}{lab} [{(r['at'] or '')[5:16]}] 用:{q}")
        if a:
            print(f"      答:{a[:160]}")

# A. 切分歧义代表：666(AI3vs人1) 328(AI7vs人11) 1195(AI2vs人1) 279(AI2vs人3)
# 已审阅，注释掉避免重复输出
# for cid in ("666", "328", "1195", "279"):
#     dump_conv(cid, "【切分歧义】")

# B. 标签抽查：各标签抽 4 段（真实组、已标）
pick = {"直答正确": [], "未直答": [], "未覆盖": []}
cands = []
for cid_s, lab_map in labels.items():
    if cid_s not in convs or convs[cid_s]["is_tester"]:
        continue
    for k, v in lab_map.items():
        if v in pick:
            cands.append((cid_s, int(k), v))
random.shuffle(cands)
for cid, k, v in cands:
    if len(pick[v]) < 4 and all(x[0] != cid for x in pick[v]):
        pick[v].append((cid, k, v))
for v, lst in pick.items():
    for cid, k, _ in lst:
        dump_conv(cid, f"【标签抽查:{v} 段首#{k}】")
