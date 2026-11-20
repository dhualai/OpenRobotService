"""只读检测：messages 表倒挂/合并异常（连生产库，仅 SELECT）。

检测项：
A. sequence 重复（同会话同序号多条）
B. 相邻同 role（sequence 相邻且 role 相同——两句 AI 被当一个回答的落库形态）
C. 时间倒挂（sequence 后一条 created_at 早于前一条）
D. 样本回显（抽问题会话按序打印 role/时间/内容首行，人工核对）
"""
import re

import pymysql

env = open("/data/apps/OpenRobotService/ai/.env", encoding="utf-8").read()
url = next(l for l in env.splitlines() if l.startswith("DATABASE_URL="))
m = re.search(r"//([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/(\w+)", url)
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2),
                       database=m.group(5), charset="utf8mb4")
cur = conn.cursor()

print("=== 总量 ===")
cur.execute("SELECT COUNT(*) FROM messages")
print("messages 总数:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM conversations")
print("conversations 总数:", cur.fetchone()[0])

print("\n=== A. sequence 重复（同会话同序号）===")
cur.execute("""
SELECT conversation_id, sequence, COUNT(*) c
FROM messages GROUP BY conversation_id, sequence HAVING c > 1
ORDER BY c DESC LIMIT 15
""")
rows = cur.fetchall()
print(f"命中 {len(rows)} 组（上限15）：")
for r in rows:
    print(f"  conv={r[0]} seq={r[1]} x{r[2]}")

print("\n=== B. 相邻同 role（sequence 相邻、role 相同）===")
cur.execute("""
SELECT m1.conversation_id, m1.role, COUNT(*) n, MIN(m1.sequence), MAX(m1.sequence)
FROM messages m1
JOIN messages m2 ON m1.conversation_id = m2.conversation_id
              AND m1.sequence = m2.sequence + 1
WHERE m1.role = m2.role
GROUP BY m1.conversation_id, m1.role ORDER BY n DESC LIMIT 15
""")
rows = cur.fetchall()
print(f"命中会话 {len(rows)} 个（上限15，按异常条数倒排）：")
for r in rows:
    print(f"  conv={r[0]} role={r[1]} 相邻同role对数={r[2]} seq范围[{r[3]},{r[4]}]")

print("\n=== B2. 相邻同 role 全量统计（不分会话）===")
cur.execute("""
SELECT m1.role, COUNT(*)
FROM messages m1
JOIN messages m2 ON m1.conversation_id = m2.conversation_id
              AND m1.sequence = m2.sequence + 1
WHERE m1.role = m2.role
GROUP BY m1.role
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]} 对")

print("\n=== C. 时间倒挂（后一条 created_at 早于前一条，按 sequence）===")
cur.execute("""
SELECT m1.conversation_id, COUNT(*) n
FROM messages m1
JOIN messages m2 ON m1.conversation_id = m2.conversation_id
              AND m1.sequence = m2.sequence + 1
WHERE m2.created_at > m1.created_at
GROUP BY m1.conversation_id ORDER BY n DESC LIMIT 15
""")
rows = cur.fetchall()
print(f"命中会话 {len(rows)} 个（上限15）：")
for r in rows:
    print(f"  conv={r[0]} 倒挂对数={r[1]}")

# D. 样本：取相邻同 assistant 最多的会话回显
print("\n=== D. 样本回显（最严重会话，按 sequence 打印前 30 条）===")
cur.execute("""
SELECT m1.conversation_id
FROM messages m1
JOIN messages m2 ON m1.conversation_id = m2.conversation_id
              AND m1.sequence = m2.sequence + 1
WHERE m1.role = 'assistant' AND m1.role = m2.role
GROUP BY m1.conversation_id ORDER BY COUNT(*) DESC LIMIT 1
""")
top = cur.fetchone()
if top:
    cid = top[0]
    print(f"会话 {cid} 消息序列：")
    cur.execute("""
    SELECT sequence, role, created_at,
           REPLACE(REPLACE(SUBSTRING(content, 1, 60), '\n', ' '), '\r', '')
    FROM messages WHERE conversation_id = %s ORDER BY sequence LIMIT 30
    """, (cid,))
    for r in cur.fetchall():
        print(f"  seq={r[0]:<3} {r[1]:<9} {r[2]} | {r[3]}")

conn.close()
print("\nAUDIT_DONE")
