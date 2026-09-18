"""只读检测③：排序方案对比——现键 (seq, USER前, id) vs 新键 (created_at, USER前, id)，
统计各产生多少「相邻同 ASSISTANT 对」（两句 AI 被并成一个回答的直接诱因）。"""
import re
from collections import defaultdict

import pymysql

env = open("/data/apps/OpenRobotService/ai/.env", encoding="utf-8").read()
url = next(l for l in env.splitlines() if l.startswith("DATABASE_URL="))
m = re.search(r"//([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/(\w+)", url)
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2),
                       database=m.group(5), charset="utf8mb4")
cur = conn.cursor()

cur.execute("SELECT conversation_id, role, sequence, created_at, id FROM messages")
by_conv = defaultdict(list)
for cid, role, seq, created, mid in cur.fetchall():
    by_conv[cid].append((seq, role, created, mid))


def adjacent_pairs(order):
    n = 0
    for i in range(len(order) - 1):
        if order[i][1] == "ASSISTANT" and order[i + 1][1] == "ASSISTANT":
            n += 1
    return n


old_total = new_total = 0
old_convs = new_convs = 0
worse = []
for cid, lst in by_conv.items():
    old = sorted(lst, key=lambda x: (x[0] or 0, 0 if x[1] == "USER" else 1, x[3]))
    new = sorted(lst, key=lambda x: (x[2], 0 if x[1] == "USER" else 1, x[3]))
    a, b = adjacent_pairs(old), adjacent_pairs(new)
    old_total += a
    new_total += b
    if a:
        old_convs += 1
    if b:
        new_convs += 1
    if b > a:
        worse.append((cid, a, b))

print(f"现排序 (seq,USER前,id)：相邻 ASSISTANT 对={old_total}，涉及会话={old_convs}")
print(f"新排序 (created_at,USER前,id)：相邻 ASSISTANT 对={new_total}，涉及会话={new_convs}")
print(f"\n新排序比旧排序更差的会话 {len(worse)} 个（上限10）：")
for cid, a, b in worse[:10]:
    print(f"  conv={cid} 旧={a} 新={b}")
conn.close()
print("\nAUDIT3_DONE")
