# -*- coding: utf-8 -*-
"""直答率 L1 统计（口径见 docs/直答率统计口径.md）。

数据链（已验证）：conversations/messages ← metadata_.ai_session_id ← tickets.session_id。
口径：有效回合 = 实质提问（LLM 离线判）∧ 非测试噪声（测试人员∧命中猜你想问题库，交集剔除）。
输出：会话级/回合级 1-转工单率 + 伴生信号 + L2 人工审核底表 CSV。

用法：
  python ai/scripts/direct_answer_rate.py --since 2026-09-01 --until 2026-09-07
      [--testers ai/data/testers.json]      # 测试人员 user_id 列表（JSON 数组）
      [--out OpenRobotService_Data]         # 输出目录（CSV/JSON 落这）
      [--no-llm]                            # 跳过 LLM 判定（courtesy 全算提问，快速粗算）
      [--limit 50]                          # 只处理前 N 会话（调试）
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
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)
os.chdir(_PROJ)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJ, "ai", ".env"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from ai.core.database import SessionLocal

QUESTION_BANK = os.path.join(_PROJ, "ai", "data", "suggested_questions.json")


# ----------------------------------------------------------------
# 数据拉取（只读）
# ----------------------------------------------------------------
def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def fetch_data(since: datetime, until: datetime, limit: int = 0):
    db = SessionLocal()
    try:
        q = ("SELECT id, user_id, created_at, metadata_, service_ticket_id "
             "FROM conversations WHERE created_at >= %s AND created_at < %s "
             "ORDER BY created_at")
        if limit:
            q += f" LIMIT {int(limit)}"
        convs = db.execute(q, (since, until)).fetchall()

        conv_ids = [c[0] for c in convs]
        turns = []
        if conv_ids:
            ph = ",".join(["%s"] * len(conv_ids))
            turns = db.execute(
                f"SELECT conversation_id, role, content, created_at, sequence "
                f"FROM messages WHERE conversation_id IN ({ph}) "
                f"ORDER BY conversation_id, sequence, id", conv_ids).fetchall()

        tickets = db.execute(
            "SELECT session_id, created_at FROM tickets "
            "WHERE created_at >= %s AND created_at < %s", (since, until)).fetchall()
        return convs, turns, tickets
    finally:
        db.close()


def merge_rounds(turns: list) -> dict:
    """回合 = 一条 user 消息 + 其后紧邻的 assistant 消息（拼接）。"""
    by_conv: dict = {}
    for conv_id, role, content, created_at, seq in turns:
        by_conv.setdefault(conv_id, []).append(
            {"role": role, "content": content or "", "at": created_at, "seq": seq})
    rounds_by_conv = {}
    for conv_id, msgs in by_conv.items():
        rounds = []
        cur = None
        for m in msgs:
            if m["role"] == "user":
                if cur:
                    rounds.append(cur)
                cur = {"question": m["content"], "answers": [], "at": m["at"]}
            elif m["role"] == "assistant" and cur is not None:
                cur["answers"].append(m["content"])
        if cur:
            rounds.append(cur)
        rounds_by_conv[conv_id] = rounds
    return rounds_by_conv


# ----------------------------------------------------------------
# 噪声与机械信号
# ----------------------------------------------------------------
def _norm(s: str) -> str:
    return "".join(s.split()).lower()


def load_question_bank() -> set:
    if not os.path.exists(QUESTION_BANK):
        print(f"[WARN] 题库缺失 {QUESTION_BANK}，测试噪声只能按身份判（更保守）")
        return set()
    with open(QUESTION_BANK, encoding="utf-8") as fh:
        items = json.load(fh)
    return {_norm(it.get("question", "")) for it in items if it.get("question")} | \
           {_norm(a) for it in items for a in it.get("aliases", []) if a}


def load_testers(path: str) -> set:
    if not path or not os.path.exists(path):
        print(f"[WARN] 测试名单缺失（{path}），无名单则不剔除任何会话——请补名单后再出正式数")
        return set()
    with open(path, encoding="utf-8") as fh:
        return {str(x).strip() for x in json.load(fh) if str(x).strip()}


# ----------------------------------------------------------------
# LLM 回合判定（批量：一会话一次调用；判断全交大模型，无关键词表）
# ----------------------------------------------------------------
async def classify_rounds(rounds: list) -> list:
    """返回 [{q: 是否实质提问, t: 回答是否明确建议转工单}]，长度同 rounds。失败降级 q=True。
    末元素 {"n_topics": 会话内独立问题数}（0907 加：测多问题会话占比，决定 L1
    用会话粒度还是需要回合切分——纯统计透出，不参与分母计算）。"""
    fallback = [{"q": True, "t": False} for _ in rounds] + [{"n_topics": 0}]
    if not rounds:
        return fallback
    try:
        from ai.core import get_intent_client
        llm = await get_intent_client()
        lines = []
        for i, r in enumerate(rounds):
            ans_head = (r["answers"][0] if r["answers"] else "")[:120]
            lines.append(f"{i}. 用户说：{(r['question'] or '')[:200]} → 助手答（开头）：{ans_head}")
        prompt = (
            "下面是一场客服对话里用户的每条消息和助手回答的开头。逐条判断：\n"
            "q=这条用户消息是否是实质提问或求助（问候、闲聊、确认收到、纯表情不是）；\n"
            "t=助手回答是否明确建议用户转工单/提单（仅口头建议，与用户是否实际提单无关）。\n"
            "另估计 n=本场对话包含几个**独立的用户问题**（同一问题的澄清/追问/换问法算同一个，"
            "整数，纯闲聊为 0）。\n"
            "只输出 JSON：{\"rounds\": [{\"i\":0,\"q\":true,\"t\":false}, ...], \"n\": 1}，不要输出其他内容。\n\n"
            + "\n".join(lines)
        )
        raw = await llm.complete(prompt=prompt, max_tokens=1000, temperature=0,
                                 thinking=False)
        obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        arr = obj.get("rounds") or []
        out = [{"q": True, "t": False} for _ in rounds]
        for it in arr:
            i = int(it.get("i", -1))
            if 0 <= i < len(out):
                out[i] = {"q": bool(it.get("q", True)), "t": bool(it.get("t", False))}
        try:
            out.append({"n_topics": max(0, min(int(obj.get("n", 0)), len(rounds)))})
        except (TypeError, ValueError):
            out.append({"n_topics": 0})
        return out
    except Exception as e:
        print(f"  [WARN] LLM 判定失败（降级全算提问）: {type(e).__name__}: {e}")
        return fallback


# ----------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="起始日 2026-09-01")
    ap.add_argument("--until", required=True, help="截止日（不含）2026-09-08")
    ap.add_argument("--testers", default=os.path.join(_PROJ, "ai", "data", "testers.json"))
    ap.add_argument("--out", default="D:/Code/OpenRobotService_Data")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    since, until = _parse_ts(args.since), _parse_ts(args.until)
    testers = load_testers(args.testers)
    bank = load_question_bank()
    print(f"区间 {since} ~ {until}｜名单 {len(testers)} 人｜题库 {len(bank)} 条")

    convs, turns, tickets = fetch_data(since, until, args.limit)
    print(f"会话 {len(convs)}｜消息 {len(turns)}｜区间内工单 {len(tickets)}")
    rounds_by_conv = merge_rounds(turns)

    # session_id → 工单时间列表（工单反查：metadata_.ai_session_id，service_ticket_id 兜底）
    sess_map = {}
    for cid, user_id, created_at, metadata_, stid in convs:
        sess = ""
        try:
            sess = (json.loads(metadata_) or {}).get("ai_session_id", "")
        except Exception:
            pass
        sess_map[cid] = {"sess": sess or (stid or ""), "user": user_id,
                         "at": created_at}
    ticket_by_sess: dict = {}
    for t_sess, t_at in tickets:
        ticket_by_sess.setdefault(t_sess, []).append(t_at)

    rows = []  # L2 审核底表
    n_conv_total = len(convs)
    n_conv_valid = n_conv_conv_test = n_conv_ticket = 0
    n_round_question = n_round_noise = n_round_ticket = n_round_suggest = 0
    n_empty_answer = 0
    from collections import Counter
    topic_dist = Counter()  # 有效会话的独立问题数分布（测 L1 会话粒度的误差）

    for cid, user_id, created_at, metadata_, stid in convs:
        info = sess_map[cid]
        is_tester = info["user"] in testers
        rounds = rounds_by_conv.get(cid, [])
        if not rounds:
            continue
        # 会话内 LLM 判定（返回 = 逐回合判定 + 末尾 n_topics 统计）
        if args.no_llm:
            cls = [{"q": True, "t": False} for _ in rounds] + [{"n_topics": 0}]
        else:
            cls = await classify_rounds(rounds)
        round_cls = cls[:len(rounds)]
        n_topics = cls[-1].get("n_topics", 0) if len(cls) > len(rounds) else 0
        conv_ticket_times = ticket_by_sess.get(info["sess"], [])

        conv_has_question = False
        conv_is_pure_test = True
        for i, (r, c) in enumerate(zip(rounds, round_cls)):
            if not c["q"]:
                continue  # courtesy 轮，不进分母
            hit_bank = _norm(r["question"] or "") in bank if bank else False
            is_noise = is_tester and hit_bank
            empty_ans = not any((a or "").strip() for a in r["answers"])
            if empty_ans:
                n_empty_answer += 1
                continue
            if is_noise:
                n_round_noise += 1
                continue
            n_round_question += 1
            conv_has_question = True
            conv_is_pure_test = False
            # 工单归属近似：回合后 30 分钟内该会话有工单 → 计转单回合
            ticketed = any(r["at"] <= t <= r["at"] + timedelta(minutes=30)
                           for t in conv_ticket_times)
            suggests = c["t"] and not ticketed
            if ticketed:
                n_round_ticket += 1
            if suggests:
                n_round_suggest += 1
            rows.append({
                "conversation_id": cid, "user_id": user_id,
                "time": str(r["at"]), "question": (r["question"] or "")[:200],
                "answer_head": ((r["answers"][0] if r["answers"] else "") or "")[:120],
                "round_ticketed": int(ticketed), "suggest_no_ticket": int(suggests),
                "tester_hit_bank": int(is_tester and hit_bank),
            })
        if conv_has_question:
            n_conv_valid += 1
            topic_dist[n_topics] += 1
            if conv_ticket_times:
                n_conv_ticket += 1
        elif any(c["q"] for c in round_cls):
            n_conv_conv_test += 1  # 有提问但全是测试噪声

    # ---------------- 报告 ----------------
    def pct(a, b):
        return f"{(a / b * 100):.1f}%" if b else "—"

    print("\n" + "=" * 56)
    print("L1 直答率（1 − 转工单率，上界近似）")
    print("-" * 56)
    print(f"会话级：有效会话 {n_conv_valid}，其中产生工单 {n_conv_ticket} → "
          f"直答率 {pct(n_conv_valid - n_conv_ticket, n_conv_valid)}")
    print(f"回合级：有效回合 {n_round_question}，其中转单回合 {n_round_ticket} → "
          f"直答率 {pct(n_round_question - n_round_ticket, n_round_question)}")
    print("-" * 56)
    print("伴生信号（不进直答率，单独盯）")
    print(f"  建议转工单但未提单：{n_round_suggest} 回合")
    print(f"  空回答（上游故障，已排除出分母）：{n_empty_answer}")
    print(f"  测试噪声回合（名单∧题库，已剔除）：{n_round_noise}")
    print(f"  courtesy/非提问轮：已跳过（LLM 离线判定）")
    if sum(topic_dist.values()):
        multi = sum(v for k, v in topic_dist.items() if k >= 2)
        print(f"  会话问题数分布（{dict(sorted(topic_dist.items()))}）→ "
              f"多问题会话占 {pct(multi, sum(topic_dist.values()))}"
              f"（占比高则 L1 会话粒度偏差大，需回合切分）")
    print("=" * 56)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, f"direct_answer_review_{stamp}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    json_path = os.path.join(args.out, f"direct_answer_summary_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({
            "since": str(since), "until": str(until),
            "conversations_total": n_conv_total, "conversations_valid": n_conv_valid,
            "conversations_ticketed": n_conv_ticket,
            "rounds_question": n_round_question, "rounds_ticket": n_round_ticket,
            "rounds_suggest_no_ticket": n_round_suggest,
            "rounds_test_noise": n_round_noise, "rounds_empty_answer": n_empty_answer,
            "topic_dist": dict(topic_dist),
            "llm_used": not args.no_llm,
        }, fh, ensure_ascii=False, indent=1)
    print(f"L2 审核底表（{len(rows)} 回合）: {csv_path}")
    print(f"汇总 JSON: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
