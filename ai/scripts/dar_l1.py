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
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 不换 wrapper 对象：pytest 捕获下替换会炸
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
os.chdir(_PROJ)

import dar_segs  # noqa: E402  段首统一口径：bounds 优先 + 老窗口续聊追加（0920）

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

CONCURRENCY = 8
ENV = os.environ.get("DAR_ENV", "test")
DATA = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}/processed/conversations_split.jsonl"

# 话题类型枚举（抽象表述，禁具体设备示例）：周报下钻矩阵/失败清单的聚类维度
TOPIC_TYPES = ["故障处置", "操作指引", "功能咨询", "状态查询", "资料查询", "其他"]


# 长会话分窗：单次输出 max_tokens 有限，rounds 过多时 JSON 被截断、
# 后半段落回默认值（q=true/topic=0）——切分质量崩。按窗滑判，窗口间话题续号。
CLASSIFY_WINDOW = 20
# 相邻两轮隔这么久一律切新话题（代码硬切，不依赖模型自觉；见 _force_time_breaks）
TIME_BREAK_HOURS = 12

# 切分判据模板（0929 抽出）：__CONT__/__LINES__ 运行时替换。md5 进签名——
# 改判据 = 签名变 = 全量重切（历史会话内容不变，签名同就该复用，重切是白烧
# LLM 且切分漂移会错位人工标签锚点）
CLASSIFY_PROMPT_TMPL = (
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
    "__CONT__"
    "只输出 JSON：{\"rounds\": [{\"i\":0,\"topic\":0,\"q\":true,\"t\":false}, ...],"
    " \"topics\": [{\"topic\":0,\"type\":\"操作指引\"}, ...], \"n\": 2}，"
    "不要输出其他内容。\n\n"
    "__LINES__"
)


def _force_time_breaks(rounds, out, gap_hours=TIME_BREAK_HOURS):
    """相邻两轮隔超过 gap_hours 一律切成新话题（在 LLM 判定之上硬切）。

    1106：判据里本来就写了「时间上隔很久（隔几小时、隔天）再回来提新的事算新话题」，
    但模型经常不执行——梅健 cid 1400 第 5-17 轮跨 17 天、含 8 个不同问题，
    被压成一个话题；叶绍阳 86 轮压成 3 个。时间跨度是确定信号，不依赖模型自觉。
    只切不并：LLM 自己切的边界全部保留，编号重新连续化。
    """
    if not out:
        return out
    # classify 的返回值末尾带 {"n_topics": N}，先摘掉再比对长度
    body = out[:-1] if isinstance(out[-1], dict) and "n_topics" in out[-1] else out
    if len(body) != len(rounds):
        return out
    new_topic = -1
    last_llm = None
    prev_t = None
    for i, k in enumerate(body):
        try:
            t = datetime.fromisoformat((rounds[i].get("at") or "").split(".")[0])
        except ValueError:
            t = None
        broke = (prev_t is not None and t is not None
                 and (t - prev_t).total_seconds() > gap_hours * 3600)
        llm_topic = k.get("topic")
        if broke or llm_topic != last_llm:
            new_topic += 1
        last_llm = llm_topic
        k["topic"] = new_topic
        prev_t = t
    return out


async def classify(rounds):
    """逐回合 {q,t} + 末尾 {n_topics}。失败降级全算提问（方向：保守）。
    超过 CLASSIFY_WINDOW 条按窗滑判，窗口间话题编号续接。"""
    if len(rounds) <= CLASSIFY_WINDOW:
        return _force_time_breaks(rounds, await _classify_once(rounds, 0))
    out = []
    base = 0
    for s in range(0, len(rounds), CLASSIFY_WINDOW):
        part = await _classify_once(rounds[s:s + CLASSIFY_WINDOW], base)
        base += part[-1]["n_topics"]
        out.extend(part[:-1])
    out.append({"n_topics": base})
    return _force_time_breaks(rounds, out)


async def _classify_once(rounds, topic_base):
    """单窗判定：topic 编号从 topic_base 起续接（0=完整会话）。"""
    fb = [{"q": True, "t": False, "topic": topic_base} for _ in rounds] + [
        {"n_topics": (1 if topic_base else 0)}]
    if not rounds:
        return fb
    try:
        from dar_llm import get_dar_client
        llm = await get_dar_client()
        lines = []
        for i, r in enumerate(rounds):
            # 每条 AI 回答的开头都给（不只第一条——多段回答/追问的判定需要全貌）
            heads = " / ".join(((a or "")[:80]).strip() for a in (r.get("a") or []))
            when = (r["at"] or "")[5:16]
            lines.append(f"{i}. [{when}] 用户说：{(r['q'] or '')[:300]} → 助手答（开头）：{heads[:220]}")
        cont = (
            f"\n（这是长对话的中段：前文已有 {topic_base} 个话题，本段话题编号从 {topic_base} 起；"
            f"若本段开头延续前面的话题，请继续用编号 {max(0, topic_base - 1)}。）\n"
            if topic_base else ""
        )
        prompt = CLASSIFY_PROMPT_TMPL.replace(
            "__CONT__", cont).replace("__LINES__", "\n".join(lines))
        raw = await llm.complete(prompt=prompt, max_tokens=4000, temperature=0,
                                 thinking=False)
        obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        out = [{"q": True, "t": False, "topic": topic_base} for _ in rounds]
        for it in obj.get("rounds") or []:
            i = int(it.get("i", -1))
            if 0 <= i < len(out):
                out[i] = {"q": bool(it.get("q", True)), "t": bool(it.get("t", False)),
                          "topic": max(0, int(it.get("topic", 0) or 0))}
        # topic 编号归一化（按首次出现顺序重编，防 LLM 跳号乱序）+ type 随映射同步；
        # 分窗续接：归一化从 topic_base 起编，跨窗编号天然递增不冲突
        ttype = {}
        for it in obj.get("topics") or []:
            try:
                ttype[int(it.get("topic", -1))] = str(it.get("type", "其他"))[:12]
            except (TypeError, ValueError):
                continue
        remap, nxt, tmap = {}, topic_base, {}
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


def _segments(cls):
    """按相邻 topic 变化切段，返回每段的回合索引列表（与 dar_l3/检索判定/标注工具同规则）。

    原按 topic 值归组会把「0,1,0」这类回头话题并成一段，真实组比下游少 7 段
    （0909 实锤：归组 451 段/有问答 411 vs 变化 459 段/有问答 418）。
    人工切分（--review）会把 topic 改写成 0..n-1 连续编号，按变化断出的边界与人工一致。
    """
    if not cls:
        return []
    starts = [0] + [i for i in range(1, len(cls))
                    if cls[i].get("topic", 0) != cls[i - 1].get("topic", 0)]
    return [list(range(s, starts[tid + 1] if tid + 1 < len(starts) else len(cls)))
            for tid, s in enumerate(starts)]


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
    # 切分签名：模型 + 切分判据 md5（同步可算，全量分支与 review 写回共用；
    # 签名同的历史切分才复用——换模型/改判据自动全量重切）
    import hashlib as _hl
    cur_sig = (f"{os.getenv('DAR_MODEL', 'deepseek-flash')}|"
               f"{_hl.md5(CLASSIFY_PROMPT_TMPL.encode('utf-8')).hexdigest()[:8]}"
               f"|w{CLASSIFY_WINDOW}|tb{TIME_BREAK_HOURS}v2")

    if args.replay:
        print(f"replay：读 {cls_path}，不调 LLM")
        saved = {j["conversation_id"]: j for j in
                 (json.loads(l) for l in open(cls_path, encoding="utf-8"))}
        n_pad = 0
        for c in convs:
            j = saved.get(c["conversation_id"], {})
            cls = list(j.get("cls") or [])
            nr = len(c["rounds"])
            # 1106：续聊会让 rounds 变长而存档 cls 还是旧的短版——replay 不调
            # LLM，用末项标签补齐（不丢轮次），真正重切在非 replay 分支。
            if len(cls) < nr:
                pad = dict(cls[-1]) if cls else {"q": True, "t": False, "topic": 0}
                cls += [dict(pad) for _ in range(nr - len(cls))]
                n_pad += 1
            elif len(cls) > nr:
                cls = cls[:nr]
            c["_cls"] = cls or [{"q": True, "t": False, "topic": 0}
                                for _ in range(nr)]
        if n_pad:
            print(f"⚠️ {n_pad} 个会话存档切分短于实际轮数，已按末段标签补齐"
                  f"（跑一次 l1 会正式重切）")
    else:
        # 0929 按会话增量：classified 行带 sig（模型+切分判据 md5），一致的历史
        # 会话直接复用（历史会话内容不变，重切=白烧 LLM 且切分漂移会错位人工
        # 标签锚点）；只有新会话/签名变才重切。人工边界不受影响——--review 的
        # 覆盖写回在本分支之后独立执行，恒优先。
        saved = {}
        if os.path.exists(cls_path):
            for l in open(cls_path, encoding="utf-8"):
                if l.strip():
                    j = json.loads(l)
                    if j.get("sig") == cur_sig:
                        saved[j["conversation_id"]] = j
        # 1106：续聊会话的轮数会变长，而 sig 只看模型+判据——只比 sig 会把 8 月
        # 那份短切分直接套到 10/11 月追加的轮次上。cls 长度≠rounds 的后果：
        # L1 整会话跳过不计数、切题页并成一个话题（用户实锤 CID 339）。
        # 复用必须同时要求轮数一致，变长的会话重切。
        def _reusable(c):
            j = saved.get(c["conversation_id"])
            return bool(j) and len(j.get("cls") or []) == len(c["rounds"])

        reuse = [c for c in convs if _reusable(c)]
        todo_convs = [c for c in convs if not _reusable(c)]
        n_grow = sum(1 for c in convs
                     if c["conversation_id"] in saved and not _reusable(c))
        print(f"会话 {len(convs)}：增量复用 {len(reuse)}（sig={cur_sig}），"
              f"LLM 切分 {len(todo_convs)} 个"
              + (f"（其中 {n_grow} 个因续聊轮数变化重切）" if n_grow else ""))
        for c in reuse:
            c["_cls"] = saved[c["conversation_id"]]["cls"]
        if todo_convs:
            t0 = time.time()
            sem = asyncio.Semaphore(CONCURRENCY)
            done = [0]

            async def one(c):
                async with sem:
                    res = await classify(c["rounds"])
                    done[0] += 1
                    if done[0] % 20 == 0 or done[0] == len(todo_convs):
                        print(f"  {done[0]}/{len(todo_convs)}"
                              f"（{done[0] / len(todo_convs) * 100:.0f}%，{time.time()-t0:.0f}s）")
                    c["_cls"] = res[:len(c["rounds"])]

            await asyncio.gather(*(one(c) for c in todo_convs))
            print(f"切分完成，{time.time()-t0:.0f}s。")
        with open(cls_path, "w", encoding="utf-8") as fh:
            for c in convs:
                fh.write(json.dumps({"conversation_id": c["conversation_id"],
                                     "cls": c["_cls"], "sig": cur_sig},
                                    ensure_ascii=False) + "\n")
        # 校对入口：build_segmentation_tool.py 生成的 segmentation_tool.html
        print("话题校对工具: segmentation_tool.html（build_segmentation_tool.py 重新生成）")

    # 人工切分覆盖 + L2 标注（segmentation_tool.html 导出的 manual_segmentation.json：
    # bounds={convId:[新段首回合索引,...]}（0 恒在）；labels={convId:{段首回合索引: 六类标签}}）
    if args.review and os.path.exists(args.review):
        rev = json.load(open(args.review, encoding="utf-8"))
        seg = rev.get("bounds") or {}
        lab_all = rev.get("labels") or {}
        legacy = {"直答错误": "未直答", "直答不完整": "未直答"}
        n_fix = 0
        for c in convs:
            c["_labels"] = {int(k): legacy.get(v, v) for k, v in
                            (lab_all.get(str(c["conversation_id"])) or {}).items()
                            if str(k).isdigit()}
            b = seg.get(str(c["conversation_id"]))
            if not b:
                continue
            n_before = len({k["topic"] for k in c["_cls"]})
            # 段首统一展开（0920）：续聊追加的新段在 topic 重写时一并成段，
            # 与漏斗/标注工具口径一致（新段 topic 独立编号，等下一轮判定）
            starts = dar_segs.effective_starts(
                c.get("rounds") or [], str(c["conversation_id"]),
                seg, lab_all, frozen_len=rev.get("frozen_len") or {},
                n=len(c["_cls"]))
            for tid, s in enumerate(starts):
                e = starts[tid + 1] if tid + 1 < len(starts) else len(c["_cls"])
                for i in range(s, e):
                    c["_cls"][i]["topic"] = tid
            if len(starts) != n_before:
                n_fix += 1
        print(f"人工切分覆盖 {len(seg)} 个会话（其中段数有变化 {n_fix} 个）")
        # 覆盖后写回 classified.jsonl（0915 修 bug：原先只在 LLM 判定后写盘、
        # review 覆盖仅改内存且 replay 模式不写——落盘文件永远是 LLM 原始切分，
        # 漏斗等下游读到的边界与人工核对的不一致，人工标签按段首匹配大面积错位）
        with open(cls_path, "w", encoding="utf-8") as fh:
            for c in convs:
                fh.write(json.dumps({"conversation_id": c["conversation_id"],
                                     "cls": c["_cls"], "sig": cur_sig},
                                    ensure_ascii=False) + "\n")
        print(f"人工边界版 classified 已落盘: {cls_path}")
    print("聚合…")

    # ---------------- 聚合 ----------------
    rows = []
    stats = defaultdict(lambda: Counter())
    lab_cnt = defaultdict(Counter)
    seg_dist = Counter()
    n_bad = 0  # 切分长度≠实际轮数的会话（被整会话跳过，见下方告警）

    for c in convs:
        grp = "测试组" if c["is_tester"] else "真实组"
        month = (c["created_at"] or "")[:7]
        rounds, cls = c["rounds"], c["_cls"]
        if not rounds:
            continue
        stats[(grp, month)]["courtesy"] += sum(1 for k in cls if not k["q"])
        if not cls or len(cls) != len(rounds):
            n_bad += 1
            continue
        # 段分组：相邻 topic 变化即断段（与 dar_l3/检索判定/标注工具/工作台同规则，
        # 见 _segments——原按 topic 值归组会把「0,1,0」这类回头话题并成一段，
        # 真实组比下游少 7 段：0909 实锤 451 段/有问答 411 vs 459 段/有问答 418）
        segs = dict(enumerate(_segments(cls)))
        seg_ids = list(segs)
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

    # 1106 守卫：切分长度与实际轮数不符的会话会被整会话跳过、不进任何统计。
    # 成因是续聊后轮数变长而存档切分未更新（0901~1106 一直静默发生，43 个会话
    # 从未计入周报）。复用侧现已强制校验轮数，正常不会再出现——一旦出现就是
    # 逻辑回退，必须当场可见，不能又静默少算。
    if n_bad:
        print(f"\n🔴 告警：{n_bad} 个会话的切分长度与实际轮数不符，"
              f"已从本次统计中跳过（等于没算这些会话）。\n"
              f"    通常是切分产物过期，再跑一次 l1 会自动重切修好；"
              f"若反复出现请检查 dar_l1 的复用逻辑。\n")

    print("\n" + "=" * 72)
    print("L1 直答率（1 − 转工单率，上界近似；话题段="
          + ("人工边界已覆盖（--review）" if args.review and os.path.exists(args.review)
             else "LLM 按内容+时间切分") + "）")
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
    # 直接提单=非直答测量对象，分子分母均剔除（用户明确目的来提单）
    LAB_KEYS = ["直答正确", "未直答", "未覆盖", "直接提单", "无法判定", "未标"]
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
                  f"（未覆盖/无法判定单列；直接提单共 "
                  f"{s['直接提单']} 段已剔除，不进分子分母）")
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
