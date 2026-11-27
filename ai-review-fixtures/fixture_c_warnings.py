# 【AI 检视测试样本 C】快筛验证：warning 档混合 ×4（不推企微）
# 本文件为 AI Review 观察期测试样本（伪代码，不可运行），不合并。
# 预期快筛：关注档（黄）×4、高危 0——因此快筛只落 PR 评论、不推企微群：
#   1. 系统命令直调（命令注入 / 动态执行）
#   2. 字符串拼接 SQL
#   3. 本地时间取值落库（UTC 纪律）
#   4. 不安全反序列化
# 预期 AI 深度检视：对四类写法逐条给出影响与整改（参数化 / argv / UTC / safe_load）。

import os
import pickle
from datetime import datetime


def ping(host):
    os.system("ping -c1 " + host)              # 预期命中 1


def find_user(conn, uid):
    return conn.execute("SELECT * FROM users WHERE id = %s" % uid)   # 预期命中 2


def mark_seen(row):
    row.seen_at = datetime.now()               # 预期命中 3


def load_state(path):
    return pickle.load(open(path, "rb"))       # 预期命中 4
