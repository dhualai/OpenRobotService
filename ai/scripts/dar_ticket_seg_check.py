# -*- coding: utf-8 -*-
"""「直接提单」判定一致性：人工标签 × LLM 的 q 判定（段内有无咨询回合）。"""
import io
import json
import os
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = r"C:/Users/PAJ26020/Desktop/export_dar/processed"
convs = {str(c["conversation_id"]): c for c in
         (json.loads(l) for l in open(os.path.join(OUT, "conversations_split.jsonl"), encoding="utf-8"))}
cls_all = {j["conversation_id"]: j["cls"] for j in
           (json.loads(l) for l in open(os.path.join(OUT, "conversations_classified.jsonl"), encoding="utf-8"))}
man = json.load(open(r"C:/Users/PAJ26020/Downloads/manual_segmentation.json", encoding="utf-8"))
bounds, labels = man.get("bounds") or {}, man.get("labels") or {}
legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}

cross = Counter()
mismatch = []
for cid_s, b in bounds.items():
    c = convs.get(cid_s)
    if not c:
        continue
    cls = cls_all.get(cid_s)
    if not cls or len(cls) != len(c["rounds"]):
        continue
    grp = "测试组" if c["is_tester"] else "真实组"
    lab_map = {int(k): legacy.get(v, v) for k, v in (labels.get(cid_s) or {}).items()
               if str(k).isdigit()}
    manual = sorted({0, *(int(x) for x in b if 0 <= int(x) < len(c["rounds"]))})
    for tid, s in enumerate(manual):
        e = manual[tid + 1] if tid + 1 < len(manual) else len(c["rounds"])
        lab = lab_map.get(s, "未标")
        # LLM 判定信号：段内有无被 q=true 且有回答的回合（=有实质咨询）
        q_any = any(cls[i]["q"] for i in range(s, e))          # LLM 认为有咨询消息
        q_ans = any(cls[i]["q"] and any(a.strip() for a in c["rounds"][i]["a"])
                    for i in range(s, e))                        # 咨询且有回答
        cross[(grp, lab, "纯提单" if not q_any else ("咨询无答" if not q_ans else "有咨询"))] += 1
        if lab == "直接提单" and q_ans:
            mismatch.append((grp, cid_s, s, (c["rounds"][s]["q"] or "")[:60]))

print("== 人工标签 × LLM q 判定 ==")
labs = ["直接提单", "建议转单", "直答正确", "未直答", "未覆盖", "未标"]
for grp in ("真实组", "测试组"):
    print(f"\n{grp}:")
    for lab in labs:
        a = cross.get((grp, lab, "纯提单"), 0)
        b2 = cross.get((grp, lab, "咨询无答"), 0)
        c2 = cross.get((grp, lab, "有咨询"), 0)
        if a + b2 + c2:
            print(f"  {lab:<5}（{a+b2+c2:>3}）：LLM判纯提单 {a:>3}｜有咨询但全空答 {b2:>3}｜有咨询 {c2:>3}")

n_man = sum(cross.get(("真实组", "直接提单", k), 0) for k in ("纯提单", "咨询无答", "有咨询"))
n_agree = cross.get(("真实组", "直接提单", "纯提单"), 0)
print(f"\n真实组「直接提单」人工标 {n_man} 段，LLM 判纯提单 {n_agree} 段"
      f"（一致率 {n_agree/n_man*100:.0f}%）" if n_man else "")
print("\n人工标直接提单但 LLM 认为有咨询的段（前 15）：")
for g, cid, s, q in mismatch[:15]:
    print(f"  [{g} {cid}#{s}] {q}")
