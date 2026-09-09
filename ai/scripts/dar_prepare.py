# -*- coding: utf-8 -*-
"""直答率数据准备：按会话切分 + 测试/真实分组 + 会话↔工单关联。

输入：export_dar/{users,conversations,messages,tasks}.csv.gz（服务器导出）
输出：export_dar/processed/conversations_split.jsonl（一会话一行，含回合与工单关联）
      + 控制台汇总统计。

切分口径（docs/直答率统计口径.md）：会话=conversations 一行（前端「新建会话」），
回合=一条 user 消息 + 其后紧邻 assistant 消息（消息级，v1）。
"""
import csv
import gzip
import io
import json
import os
import sys
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

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
    # 否则排序后答案跑到问题前面，切分会丢开头的 AI 消息、回合配对错位
    by_conv = {}
    for m in msgs:
        by_conv.setdefault(m["conversation_id"], []).append(m)
    for cid, lst in by_conv.items():
        lst.sort(key=lambda x: (int(x["sequence"] or 0),
                                0 if x["role"] == "USER" else 1,
                                int(x["id"] or 0)))

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
    print(f"会话 {n_rows}（无 session_id 的 {n_no_sess} 条，无法关联工单）")
    print(f"  测试组: {n_test_conv} 会话（其中 {n_test_task_conv} 个出过工单）")
    print(f"  真实组: {n_real_conv} 会话（其中 {n_real_task_conv} 个出过工单）")
    print(f"回合总数（消息级 v1）: {n_rounds}")


if __name__ == "__main__":
    main()
