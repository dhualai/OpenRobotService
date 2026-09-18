"""只读检测②：sequence 撞号的时间分布（判断历史遗留 vs 仍在发生）+ 撞号细节。"""
import re

import pymysql

env = open("/data/apps/OpenRobotService/ai/.env", encoding="utf-8").read()
url = next(l for l in env.splitlines() if l.startswith("DATABASE_URL="))
m = re.search(r"//([^:]+):([^@]+)@([^/:]+)(?::(\d+))?/(\w+)", url)
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2),
                       database=m.group(5), charset="utf8mb4")
cur = conn.cursor()

print("=== A. sequence 撞号总览 ===")
cur.execute("""
SELECT COUNT(*) FROM (
  SELECT conversation_id, sequence FROM messages
  GROUP BY conversation_id, sequence HAVING COUNT(*) > 1
) t
""")
print("撞号 (conv, seq) 组数:", cur.fetchone()[0])

print("\n=== B. 撞号按周分布（撞号消息的 created_at 周分桶）===")
cur.execute("""
SELECT DATE_FORMAT(t.created_at, '%x-W%v') wk, COUNT(*)
FROM (
  SELECT MIN(created_at) AS created_at
  FROM messages GROUP BY conversation_id, sequence HAVING COUNT(*) > 1
) t GROUP BY wk ORDER BY wk DESC LIMIT 12
""")
for r in cur.fetchall():
    print(f"  {r[0]}  撞号组数={r[1]}")

print("\n=== C. 近7天撞号样本（还在发生吗）===")
cur.execute("""
SELECT t.conversation_id, t.sequence, m.id, m.role, m.created_at,
       REPLACE(REPLACE(SUBSTRING(m.content, 1, 40), '\n', ' '), '\r', '')
FROM (
  SELECT conversation_id, sequence, MIN(created_at) AS mc
  FROM messages GROUP BY conversation_id, sequence HAVING COUNT(*) > 1
) t
JOIN messages m ON m.conversation_id = t.conversation_id AND m.sequence = t.sequence
WHERE t.mc >= DATE_SUB(NOW(), INTERVAL 7 DAY)
ORDER BY t.conversation_id, t.sequence, m.id LIMIT 20
""")
rows = cur.fetchall()
print(f"近7天撞号明细 {len(rows)} 条（上限20）：")
for r in rows:
    print(f"  conv={r[0]} seq={r[1]} id={r[2]:<6} {r[3]:<9} {r[4]} | {r[5]}")

print("\n=== D. 同秒内 id 顺序是否可恢复正确时序（抽近7天撞号组验证）===")
# 撞号组内按 id 排：USER 应在 ASSISTANT 前（用户先说 AI 后答）
ok = bad = 0
cur.execute("""
SELECT t.conversation_id, t.sequence
FROM (
  SELECT conversation_id, sequence, MIN(created_at) AS mc
  FROM messages GROUP BY conversation_id, sequence HAVING COUNT(*) > 1
) t WHERE t.mc >= DATE_SUB(NOW(), INTERVAL 30 DAY)
LIMIT 200
""")
groups = cur.fetchall()
for cid, seq in groups:
    cur.execute("""
    SELECT id, role FROM messages
    WHERE conversation_id=%s AND sequence=%s ORDER BY id
    """, (cid, seq))
    rs = cur.fetchall()
    if len(rs) == 2 and {rs[0][1], rs[1][1]} == {"USER", "ASSISTANT"}:
        if rs[0][1] == "USER":
            ok += 1
        else:
            bad += 1
print(f"  撞号双条组（USER+ASSISTANT）：按 id 排 USER 在前 {ok} 组 / ASSISTANT 在前 {bad} 组")
print("  （USER 在前=按 id 排即可恢复正确时序）")

conn.close()
print("\nAUDIT2_DONE")
