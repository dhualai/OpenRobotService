# -*- coding: utf-8 -*-
"""直答率数据准备：按会话切分 + 测试/真实分组 + 会话↔工单关联。

输入：export_dar/{users,conversations,messages,tasks}.csv.gz（服务器导出）
输出：export_dar/processed/conversations_split.jsonl（一会话一行，含回合与工单关联）
      + 控制台汇总统计。

切分口径（docs/直答率统计口径.md）：会话=conversations 一行（前端「新建会话」），
回合=一条 user 消息 + 其后紧邻 assistant 消息（消息级，v1）。
入库倒挂（回答行排在提问行之前）在按 seq 排序后由 _repair_inversions 逐条归位。
"""
import csv
import gzip
import json
import os
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 不换 wrapper 对象：pytest 捕获下替换会炸

TESTER_NAMES = ["罗昊", "罗昊2号", "贾爽", "胡健楠", "张俊磊", "张文星", "白永奇", "耿洪秀"]
ENV = os.environ.get("DAR_ENV", "test")
DATA = rf"C:/Users/PAJ26020/Desktop/export_dar/{ENV}"
OUT = os.path.join(DATA, "processed")
TICKET_WINDOW = timedelta(minutes=30)  # 回合→工单归属近似窗口


def load(name):
    with gzip.open(os.path.join(DATA, f"{name}.csv.gz"), "rt", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ts(s):
    try:
        return datetime.fromisoformat((s or "").split(".")[0])
    except ValueError:
        return None


def parse_meta(s):
    """后端 safe_json_dumps 可能双重编码（前端 readAiSessionId 同款坑），parse 到出 dict 为止。"""
    for _ in range(2):
        try:
            v = json.loads(s)
        except Exception:
            return {}
        if isinstance(v, dict):
            return v
        s = v
    return {}


def _share_substr(a, b, n=5):
    """a 的任一 n 字连续片段出现在 b 中（判「这条回答是不是在答这条提问」）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if len(a) < n or len(b) < n:
        return False
    return any(a[i:i + n] in b for i in range(len(a) - n + 1))


def _repair_inversions(lst):
    """入库倒挂修复：DB 里少数会话的回答行排在它回答的提问行之前（id/seq 更小、
    created_at 与提问同秒），排序后回答会挂到上一轮——表现为某轮没人答、上一轮
    多一句没头没尾的回答。只搬两类高置信情形，其余保持原序：

    1. 同秒 + 内容实锤：助手消息后面紧跟一条同秒的用户消息，且两者文本有 ≥5 字
       连续公共片段（回答引用了问题原文）→ 移到该用户消息之后；
    2. 开头孤儿块：会话开头的助手消息（正常对话不会以 AI 回答开场）→ 整体归给
       第一条用户消息（原逻辑这类消息没有归属回合，直接丢弃）。

    只重排助手消息、不动用户消息顺序，所以回合编号（astart）不变。
    返回 (新序列, 移动条数)。
    """
    out, moves, i = [], 0, 0
    lead = []
    while i < len(lst) and lst[i]["role"] != "USER":
        lead.append(lst[i])
        i += 1
    if lead and i < len(lst):
        out.append(lst[i])
        out.extend(lead)
        moves += len(lead)
        i += 1
    else:
        out.extend(lead)  # 整会话无用户消息（异常数据）：原样保留
    while i < len(lst):
        m = lst[i]
        if m["role"] == "USER":
            out.append(m)
            i += 1
            continue
        nxt = lst[i + 1] if i + 1 < len(lst) else None
        if (nxt is not None and nxt["role"] == "USER"
                and m["created_at"] == nxt["created_at"]
                and _share_substr(m["content"], nxt["content"])):
            out.append(nxt)
            out.append(m)
            moves += 1
            i += 2
        else:
            out.append(m)
            i += 1
    return out, moves


def main():
    users = load("users")
    convs = load("conversations")
    msgs = load("messages")
    tasks = load("tasks")

    testers = {u["id"]: u["name"] for u in users if u["name"] in TESTER_NAMES}
    print(f"测试名单对上 {len(testers)}/{len(TESTER_NAMES)}: {sorted(testers.values())}")
    name_by_id = {u["id"]: u["name"] for u in users}

    # AI 提单 → session_id → [(created_at, task_id, title)]
    task_by_sess = {}
    for t in tasks:
        if (t.get("source") or "") != "ai":
            continue
        try:
            meta = json.loads(t.get("metadata_info") or "{}")
        except Exception:
            meta = {}
        sess = meta.get("session_id") or ""
        if sess:
            task_by_sess.setdefault(sess, []).append(
                (ts(t["created_at"]), t["id"], t["title"]))

    # messages 按 conversation 归组。同 sequence 时用户消息排前：
    # 30 个会话存在入库顺序倒挂（AI 回答先落库、用户提问后落库且 seq 撞号），
    # 否则排序后答案跑到问题前面，切分会丢开头的 AI 消息、回合配对错位。
    # 排序治不了 seq 不同的倒挂，再过一遍 _repair_inversions（逐条搬回答归位）
    by_conv = {}
    for m in msgs:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    n_fix = n_fix_conv = 0
    for cid, lst in by_conv.items():
        lst.sort(key=lambda x: (int(x["sequence"] or 0),
                                0 if x["role"] == "USER" else 1,
                                int(x["id"] or 0)))
        by_conv[cid], moves = _repair_inversions(lst)
        if moves:
            n_fix += moves
            n_fix_conv += 1

    n_rows = n_test_conv = n_test_task_conv = n_real_conv = n_real_task_conv = 0
    n_rounds = n_no_sess = 0
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "conversations_split.jsonl")
    with open(out_path, "w", encoding="utf-8") as fh:
        for c in convs:
            sess = parse_meta(c.get("metadata_")).get("ai_session_id", "")
            sess = sess or (c.get("service_ticket_id") or "")
            conv_tasks = task_by_sess.get(sess, [])
            rounds = []
            cur = None
            for m in by_conv.get(c["id"], []):
                if m["role"] == "USER":
                    if cur:
                        rounds.append(cur)
                    cur = {"q": m["content"] or "", "a": [], "at": m["created_at"],
                           "task_ids": []}
                elif m["role"] == "ASSISTANT" and cur is not None:
                    cur["a"].append(m["content"] or "")
            if cur:
                rounds.append(cur)
            for r in rounds:
                r["a"] = [a for a in r["a"] if a.strip()]
            is_tester = c["user_id"] in testers
            has_task = bool(conv_tasks)
            n_rows += 1
            if is_tester:
                n_test_conv += 1
                n_test_task_conv += has_task
            else:
                n_real_conv += 1
                n_real_task_conv += has_task
            n_rounds += len(rounds)
            if not sess:
                n_no_sess += 1
            fh.write(json.dumps({
                "conversation_id": c["id"],
                "user_id": c["user_id"],
                "name": name_by_id.get(c["user_id"], ""),
                "is_tester": is_tester,
                "created_at": c["created_at"],
                "session_id": sess,
                "n_tasks": len(conv_tasks),
                "tasks": [{"id": tid, "at": str(tat)} for tat, tid, _ in conv_tasks if tat],
                "rounds": rounds,
            }, ensure_ascii=False) + "\n")

    print(f"\n切分完成 → {out_path}")
    print(f"倒挂修复：{n_fix_conv} 个会话、{n_fix} 条助手消息归位（同秒+内容实锤 / 开头孤儿）")
    print(f"会话 {n_rows}（无 session_id 的 {n_no_sess} 条，无法关联工单）")
    print(f"  测试组: {n_test_conv} 会话（其中 {n_test_task_conv} 个出过工单）")
    print(f"  真实组: {n_real_conv} 会话（其中 {n_real_task_conv} 个出过工单）")
    print(f"回合总数（消息级 v1）: {n_rounds}")


if __name__ == "__main__":
    main()
