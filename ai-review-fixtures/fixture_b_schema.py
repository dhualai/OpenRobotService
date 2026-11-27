# 【AI 检视测试样本 B】快筛验证：schema 门禁盲区高危 ×2
# 本文件为 AI Review 观察期测试样本（描述性 SQL 字符串，不针对真实库），不合并。
# 预期快筛（两个不同标签，同文件可并存）：
#   高危「删列风险」×1——门禁不覆盖删列且不可回滚；
#   高危「改列风险」×1——门禁不覆盖改类型 / 改名。
# 预期 AI 深度检视：按 rules.md「schema 盲区」报高危，
# 理由应提及「门禁不覆盖、不可自动回滚、需确认字段已无读写方」。


def cleanup_legacy_token(conn):
    # 删列场景（预期命中 1）
    conn.execute("ALTER TABLE wechat_users DROP COLUMN legacy_token")


def widen_nickname(conn):
    # 改列类型场景（预期命中 2）
    conn.execute("ALTER TABLE wechat_users MODIFY COLUMN nickname VARCHAR(128)")
