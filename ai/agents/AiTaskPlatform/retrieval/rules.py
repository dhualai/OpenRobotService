"""任务 Agent 的附件相关常量（从 pipeline.py 拆出）

只放仍在使用的附件类型与兜底关键词配置，不依赖 AiTaskAgent 实例状态。

注：早期「按 query 关键词触发哪类能力」时代的 DISCUSS_*_KEYWORDS 与 query_matches
已删除——能力选择现由 Supervisor（LLM 调度）决定，不再走关键词路由，留着会误导
后来者以为还在生效。
"""

# 用户问了附件/日志/图片但工单无结果时的兜底告知
ATTACHMENT_MENTION_WORDS = ["日志", "附件", "图片", "截图", "log", "image", "photo"]

# ── 附件类型（diagnose / discuss 共用）──
# 直通日志管线（不走 parse_attachments，直接解压/建索引）
PIPED_LOG_EXTS = (".log", ".txt", ".csv", ".zip", ".tar", ".tgz", ".gz")
