# 【AI 检视测试样本 E】深度检视专属：项目踩坑知识库验证（pitfalls.md）
# 本文件为 AI Review 观察期测试样本（伪代码，不可运行），不合并。
# 预期快筛：关注档 ×1（本地时间取值 → UTC 纪律，快筛正则可命中）。
# 预期 AI 深度检视（打 ai-review 标签触发，按 pitfalls.md 逐条对照）：
#   1. published_at 用本地时间落库 → UTC 纪律（pitfalls #1，#333 生产事故）；
#   2. 权限码在接口函数体内注册 → 启动死锁高危（pitfalls #2，#331 事故）；
#   3. 代码读取 profile_note 新列但无配套迁移脚本 → 部署即 1054 高危
#      （pitfalls #4：部署链路不跑迁移，代码比库新直接报错）。
# 其中第 2、3 条快筛无对应规则，属 AI 语义层独有能力。

from datetime import datetime


def publish_announcement(title: str, current_user):
    register_permission("admin:announcement:publish")     # 预期 AI 2：函数体内注册
    row = {
        "title": title,
        "published_at": datetime.now(),                   # 预期快筛 + AI 1
        "profile_note": current_user.profile_note,        # 预期 AI 3：新列无迁移
    }
    return row
