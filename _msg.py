import sys, os

# 按 reword 调用顺序提供正确中文 message：
# 第 1 次（最老，0bb7fc3）= 评论WS优化；第 2 次（5032005）= 引用删除占位
MSGS = [
    'feat: 评论WS优化——在线按用户去重+头像污染、删除即时消失、'
    '微信化消息UI(头像/连续合并/气泡/绿色配色/新消息提示条)\n',
    'fix(chat): 引用消息被删除后展示已删除占位 + 自己消息底色恢复浅蓝\n',
]

idx_path = 'D:/OpenRobotService/OpenRobotService/_msgidx'
idx = 0
if os.path.exists(idx_path):
    try:
        idx = int(open(idx_path).read().strip() or 0)
    except Exception:
        idx = 0

msg = MSGS[idx] if idx < len(MSGS) else ''
with open(sys.argv[1], 'w', encoding='utf-8') as f:
    f.write(msg)

with open(idx_path, 'w') as f:
    f.write(str(idx + 1))
