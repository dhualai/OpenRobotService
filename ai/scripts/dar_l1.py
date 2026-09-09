# -*- coding: utf-8 -*-
"""直答率 L1 离线统计：读 dar_prepare.py 的切分产物，LLM 批判每个会话。

判定（一会话一次调用，判断全交大模型）：q=实质提问、t=明确建议转单、n=独立问题数。
输出：分组×分月的会话级/回合级 1−转工单率 + L2 审核底表 CSV + 汇总 JSON。

用法：
  python ai/scripts/dar_l1.py [--out C:/Users/PAJ26020/Desktop/export_dar/processed]
"""
import argparse
import asyncio
import csv
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

CONCURRENCY = 8
ENV = os.environ.get("DAR_ENV", "test")
DATA = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed/conversations_split.jsonl"

# 话题类型枚举（抽象表述，禁具体设备示例）：周报下钻矩阵/失败清单的聚类维度
TOPIC_TYPES = ["故障处置", "操作指引", "功能咨询", "状态查询", "资料查询", "其他"]


async def classify(rounds):
    """逐回合 {q,t} + 末尾 {n_topics}。失败降级全算提问（方向：保守）。"""
    fb = [{"q": True, "t": False, "topic": 0} for _ in rounds] + [{"n_topics": 0}]
    if not rounds:
        return fb
    try:
        from ai.core import get_intent_client
        llm = await get_intent_client()
        lines = []
        for i, r in enumerate(rounds):
            head = (r["a"][0] if r["a"] else "")[:120]
            when = (r["at"] or "")[5:16]
            lines.append(f"{i}. [{when}] 用户说：{(r['q'] or '')[:200]} → 助手答（开头）：{head}")
        prompt = (
            "下面是一场客服对话里用户的每条消息（含时间）和助手回答的开头。"
            "请把对话切分成话题段，再逐条判断：\n"
            "话题=一个用户诉求的完整过程：提问→澄清补充→得到解答，或者提问没解决→要求提工单→"
            "补全单据信息→提单完成。**为提单服务的消息（要求提单/报障、确认单据、补型号发图）"
            "属于引发它的前一个话题，不是新话题**。\n"
            "以下才算新话题：内容换成另一个独立的问题/诉求；或时间上隔了很久（如隔几小时、"
            "隔天）再回来提新的事。\n"
            "每条消息标注 topic=它属于第几个话题（从 0 开始，按时间顺序递增，同一话题的消息 topic 相同）。\n"
            "q=这条用户消息是否是咨询提问（想了解方法/排查解决某个问题，希望得到解答）；\n"
            "   要求提单/报障、为提单补全信息（报型号、发图片、确认单据内容）不算（q=false）；\n"
            "   问候闲聊、确认收到、纯表情、发链接不算。\n"
            "t=助手回答是否明确建议用户转工单/提单（仅口头建议，与用户是否实际提单无关）。\n"
            "另给每个话题标 type（话题类型，枚举选一）："
            "故障处置=报错/异常/不正常行为的排查修复诉求；"
            "操作指引=怎么操作、配置、使用某功能；"
            "功能咨询=功能是否存在、有什么能力、概念含义；"
            "状态查询=查某个单据/任务/数据的当前状态；"
            "资料查询=要文档、参数、清单等资料；其他=以上都不是。\n"
            "只输出 JSON：{\"rounds\": [{\"i\":0,\"topic\":0,\"q\":true,\"t\":false}, ...],"
            " \"topics\": [{\"topic\":0,\"type\":\"操作指引\"}, ...], \"n\": 2}，"
            "不要输出其他内容。\n\n"
            + "\n".join(lines)
        )
        raw = await llm.complete(prompt=prompt, max_tokens=2000, temperature=0,
                                 thinking=False)
        obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        out = [{"q": True, "t": False, "topic": 0} for _ in rounds]
        for it in obj.get("rounds") or []:
            i = int(it.get("i", -1))
            if 0 <= i < len(out):
                out[i] = {"q": bool(it.get("q", True)), "t": bool(it.get("t", False)),
                          "topic": max(0, int(it.get("topic", 0) or 0))}
        # topic 编号归一化（按首次出现顺序重编，防 LLM 跳号乱序）+ type 随映射同步
        ttype = {}
        for it in obj.get("topics") or []:
            try:
                ttype[int(it.get("topic", -1))] = str(it.get("type", "其他"))[:12]
            except (TypeError, ValueError):
                continue
        remap, nxt, tmap = {}, 0, {}
        for o in out:
            if o["topic"] not in remap:
                remap[o["topic"]] = nxt
                tmap[nxt] = ttype.get(o["topic"], "其他")
                nxt += 1
            o["topic"] = remap[o["topic"]]
        for o in out:
            o["type"] = tmap.get(o["topic"], "其他")
        out.append({"n_topics": nxt})
        return out
    except Exception as e:
        print(f"  [WARN] 判定失败（降级全算提问）: {type(e).__name__}: {e}")
        return fb


def pts(s):
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat((s or "").split(".")[0])
    except ValueError:
        return None


WINDOW = 1800  # 单归属到咨询回合的时间窗（秒）


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.dirname(DATA))
    ap.add_argument("--replay", action="store_true",
                    help="跳过 LLM，读已落盘的判定结果重算")
    ap.add_argument("--review", default="",
                    help="人工校对 CSV（manual_topic 列覆盖 LLM 话题切分）")
    args = ap.parse_args()

    convs = [json.loads(l) for l in open(DATA, encoding="utf-8")]
    cls_path = os.path.join(args.out, "conversations_classified.jsonl")

    if args.replay:
        print(f"replay：读 {cls_path}，不调 LLM")
        saved = {j["conversation_id"]: j for j in
                 (json.loads(l) for l in open(cls_path, encoding="utf-8"))}
        for c in convs:
            j = saved.get(c["conversation_id"], {})
            c["_cls"] = j.get("cls") or [{"q": True, "t": False, "topic": 0}
                                         for _ in c["rounds"]]
    else:
        print(f"会话 {len(convs)}，开始 LLM 判定（并发 {CONCURRENCY}）…")
        t0 = time.time()
        sem = asyncio.Semaphore(CONCURRENCY)
        done = [0]

        async def one(c):
            async with sem:
                res = await classify(c["rounds"])
                done[0] += 1
                if done[0] % 50 == 0:
                    print(f"  {done[0]}/{len(convs)}（{time.time()-t0:.0f}s）")
                c["_cls"] = res[:len(c["rounds"])]

        await asyncio.gather(*(one(c) for c in convs))
        print(f"判定完成，{time.time()-t0:.0f}s。")
        with open(cls_path, "w", encoding="utf-8") as fh:
            for c in convs:
                fh.write(json.dumps({"conversation_id": c["conversation_id"],
                                     "cls": c["_cls"]}, ensure_ascii=False) + "\n")
        # 校对入口：build_segmentation_tool.py 生成的 segmentation_tool.html
        print("话题校对工具: segmentation_tool.html（build_segmentation_tool.py 重新生成）")

    # 人工切分覆盖 + L2 标注（segmentation_tool.html 导出的 manual_segmentation.json：
    # bounds={convId:[新段首回合索引,...]}（0 恒在）；labels={convId:{段首回合索引: 六类标签}}）
    if args.review and os.path.exists(args.review):
        rev = json.load(open(args.review, encoding="utf-8"))
        seg = rev.get("bounds") or {}
        lab_all = rev.get("labels") or {}
        legacy = {"直答错误": "未直答", "直答不完整": "未直答", "转工单正确": "建议转单"}
        n_fix = 0
        for c in convs:
            c["_labels"] = {int(k): legacy.get(v, v) for k, v in
                            (lab_all.get(str(c["conversation_id"])) or {}).items()
                            if str(k).isdigit()}
            b = seg.get(str(c["conversation_id"]))
            if not b:
                continue
            n_before = len({k["topic"] for k in c["_cls"]})
            starts = sorted({0, *(int(x) for x in b if 0 <= int(x) < len(c["_cls"]))})
            for tid, s in enumerate(starts):
                e = starts[tid + 1] if tid + 1 < len(starts) else len(c["_cls"])
                for i in range(s, e):
                    c["_cls"][i]["topic"] = tid
            if len(starts) != n_before:
                n_fix += 1
        print(f"人工切分覆盖 {len(seg)} 个会话（其中段数有变化 {n_fix} 个）")
    print("聚合…")

    # ---------------- 聚合 ----------------
    rows = []
    stats = defaultdict(lambda: Counter())
    lab_cnt = defaultdict(Counter)
    seg_dist = Counter()

    for c in convs:
        grp = "测试组" if c["is_tester"] else "真实组"
        month = (c["created_at"] or "")[:7]
        rounds, cls = c["rounds"], c["_cls"]
        if not rounds:
            continue
        stats[(grp, month)]["courtesy"] += sum(1 for k in cls if not k["q"])
        # 段分组（topic 已归一化，按首次出现顺序）
        segs = {}
        for i, k in enumerate(cls):
            segs.setdefault(k.get("topic", 0), []).append(i)
        seg_ids = sorted(segs)
        seg_span = {s: (pts(rounds[segs[s][0]]["at"]), pts(rounds[segs[s][-1]]["at"]))
                    for s in seg_ids}
        # 每段咨询回合索引（先算，归属要用）
        seg_q = {}
        for s in seg_ids:
            seg_q[s] = [i for i in segs[s] if cls[i]["q"]
                        and any(a.strip() for a in rounds[i]["a"])]
        # 单归属：创建时间落入 [段首回合, 下一段首回合) 归该段；尾段放宽到尾回合+30min；
        # 落在无咨询的段（纯提单动作）→ 回溯到最近的前置咨询段（提单是咨询的结果）；
        # 全程无咨询 → 真·直接提单，不计转单
        seg_tasks = {s: [] for s in seg_ids}
        for t in c.get("tasks") or []:
            tat = pts(t.get("at"))
            if not tat:
                continue
            tgt = None
            for s in seg_ids:
                start = seg_span[s][0]
                if start and tat >= start:
                    tgt = s
            if tgt is None:
                continue
            last = seg_ids.index(tgt) == len(seg_ids) - 1
            limit = seg_span[tgt][1].timestamp() + WINDOW if last else float("inf")
            if tat.timestamp() > limit:
                continue
            if not seg_q[tgt]:
                for s in reversed(seg_ids[:seg_ids.index(tgt)]):
                    if seg_q[s]:
                        tgt = s
                        break
                else:
                    continue
            seg_tasks[tgt].append(t["id"])
        # 段级统计
        conv_valid = False
        for s in seg_ids:
            idxs = segs[s]
            empty = sum(1 for i in idxs if cls[i]["q"]
                        and not any(a.strip() for a in rounds[i]["a"]))
            if empty:
                stats[(grp, month)]["empty_answer"] += empty
            q_idx = seg_q[s]
            if not q_idx:
                continue
            conv_valid = True
            ticketed = bool(seg_tasks[s])
            suggests = any(cls[i]["t"] for i in q_idx) and not ticketed
            stats[(grp, month)]["segs_q"] += 1
            lab = (c.get("_labels") or {}).get(idxs[0], "")
            lab_cnt[grp][lab or "未标"] += 1
            if ticketed:
                stats[(grp, month)]["segs_ticket"] += 1
            if suggests:
                stats[(grp, month)]["suggest_no_ticket"] += 1
            rows.append({
                "group": grp, "month": month,
                "conversation_id": c["conversation_id"],
                "topic": s, "time": rounds[q_idx[0]]["at"],
                "first_question": (rounds[q_idx[0]]["q"] or "")[:200],
                "type": cls[idxs[0]].get("type") or "未分类",
                "n_rounds": len(idxs), "n_q_rounds": len(q_idx),
                "seg_ticketed": int(ticketed),
                "suggest_no_ticket": int(suggests),
                "n_tasks_conv": len(c.get("tasks") or []),
                "l2_label": lab,
            })
        if conv_valid:
            stats[(grp, month)]["convs_q"] += 1
            if any(seg_tasks[s] for s in seg_ids):
                stats[(grp, month)]["convs_ticket"] += 1
            seg_dist[len(seg_ids)] += 1

    def pct(a, b):
        return f"{a/b*100:.1f}%" if b else "—"

    print("\n" + "=" * 72)
    print("L1 直答率（1 − 转工单率，上界近似；话题段=LLM 按内容+时间切分）")
    print("-" * 72)
    print(f"{'组':　<4} {'月':<8} {'有效会话':>6} {'有单会话':>6} {'会话级':>7} "
          f"{'有效话题':>6} {'转单话题':>6} {'话题级':>7}")
    for (grp, month) in sorted(stats):
        s = stats[(grp, month)]
        cq, ct = s["convs_q"], s["convs_ticket"]
        sq, st = s["segs_q"], s["segs_ticket"]
        print(f"{grp:<4} {month:<8} {cq:>6} {ct:>6} {pct(cq-ct, cq):>7} "
              f"{sq:>6} {st:>6} {pct(sq-st, sq):>7}")
    # 全期汇总
    for grp in ("真实组", "测试组", "全部"):
        cq = sum(v["convs_q"] for (g, _), v in stats.items() if grp in (g, "全部"))
        ct = sum(v["convs_ticket"] for (g, _), v in stats.items() if grp in (g, "全部"))
        sq = sum(v["segs_q"] for (g, _), v in stats.items() if grp in (g, "全部"))
        st = sum(v["segs_ticket"] for (g, _), v in stats.items() if grp in (g, "全部"))
        print(f"{grp:<4} {'全期':<8} {cq:>6} {ct:>6} {pct(cq-ct, cq):>7} "
              f"{sq:>6} {st:>6} {pct(sq-st, sq):>7}")
    print("-" * 72)
    tot = Counter()
    for v in stats.values():
        tot.update(v)
    print(f"伴生：建议转单未提单 {tot['suggest_no_ticket']}｜空回答 {tot['empty_answer']}"
          f"｜非咨询轮 {tot['courtesy']}")
    # 话题类型分布（真实组；replay 旧判定无 type 落「未分类」，重跑判定后消失）
    tcnt = defaultdict(Counter)
    for r in rows:
        if r["group"] == "真实组":
            tcnt[r["type"]][r["seg_ticketed"]] += 1
    if tcnt:
        print("-" * 72)
        print("话题类型分布（真实组，转单率=该类出单段占比）：")
        for t, c in sorted(tcnt.items(), key=lambda kv: -sum(kv[1].values())):
            n = sum(c.values())
            print(f"  {t:<6} {n:>4} 段｜转单率 {(c[1] / n * 100):.0f}%")
    n_conv = sum(seg_dist.values())
    if n_conv:
        multi = sum(v for k, v in seg_dist.items() if k >= 2)
        print(f"会话话题数分布 {dict(sorted(seg_dist.items()))} → "
              f"多话题会话 {pct(multi, n_conv)}")
    # L2 人工标注分布（口径见 docs/直答率统计口径.md）：
    # 分子=直答正确，分母=+未直答；未覆盖/无法判定单列不摊入；
    # 建议转单/直接提单=非直答测量对象，分子分母均剔除（直接提单=用户明确目的来提单）
    LAB_KEYS = ["直答正确", "未直答", "未覆盖", "建议转单", "直接提单", "无法判定", "未标"]
    if any(sum(v[k] for k in LAB_KEYS) for v in lab_cnt.values()):
        print("-" * 72)
        for grp in ("真实组", "测试组"):
            s = lab_cnt.get(grp)
            if not s:
                continue
            ok, bad = s["直答正确"], s["未直答"]
            den = ok + bad
            e2e_den = den + s["未覆盖"]
            print(f"L2 {grp}: " + "｜".join(f"{k} {s[k]}" for k in LAB_KEYS if s[k])
                  + f" → 确定直答 {pct(ok, den)}｜端到端直答（未覆盖进分母） {pct(ok, e2e_den)}"
                  f"（未覆盖/无法判定单列；建议转单+直接提单共 "
                  f"{s['建议转单'] + s['直接提单']} 段已剔除，不进分子分母）")
    print("=" * 72)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(args.out, f"direct_answer_review_{stamp}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    json_path = os.path.join(args.out, f"direct_answer_summary_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({
            "stats": {f"{g}|{m}": dict(v) for (g, m), v in stats.items()},
            "seg_dist": dict(seg_dist), "review_rows": len(rows),
            "l2_labels": {g: {k: v[k] for k in LAB_KEYS if v[k]} for g, v in lab_cnt.items()},
        }, fh, ensure_ascii=False, indent=1)
    print(f"L2 审核底表（{len(rows)} 话题段）: {csv_path}")
    print(f"汇总: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
