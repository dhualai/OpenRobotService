import pymysql
c = pymysql.connect(host='127.0.0.1', port=3307, user='root', password='123456', database='helpdesk_7_16')
cur = c.cursor()

cur.execute("SELECT id FROM conversations WHERE service_ticket_id='persist_test_01'")
r = cur.fetchone()
if r:
    cid = r[0]
    print(f'conv_id: {cid}')
    cur.execute("SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY sequence", (cid,))
    for row in cur.fetchall():
        ct = row[1]
        print(f'  [{row[0]}] {ct[:100]}')
else:
    print('NOT FOUND')

c.close()
