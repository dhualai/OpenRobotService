"""任务 Agent 触发规则/关键词配置（从 pipeline.py 拆出散落的常量）

所有"按关键词触发哪类能力"的配置集中于此，便于维护与按产品扩展。
不依赖 AiTaskAgent 实例状态。
"""

# ── @AI 讨论（discuss）按 query 触发能力的关键词 ──
DISCUSS_LOG_KEYWORDS = ["日志", "log", ".log", ".txt"]
DISCUSS_IMG_KEYWORDS = ["图片", "截图", "照片", "image", "screenshot", "photo", "看下图", "看下图片"]
DISCUSS_HIST_KEYWORDS = ["历史", "类似", "之前", "案例", "参考"]
DISCUSS_CODE_KEYWORDS = ["代码", "源码", "怎么实现", "源代码", "逻辑是什么", "find", "看代码", "实现细节"]

# 用户问了附件/日志/图片但工单无结果时的兜底告知
ATTACHMENT_MENTION_WORDS = ["日志", "附件", "图片", "截图", "log", "image", "photo"]

# ── 附件类型（diagnose / discuss 共用）──
# 直通日志管线（不走 parse_attachments，直接解压/建索引）
PIPED_LOG_EXTS = (".log", ".txt", ".csv", ".zip", ".tar", ".tgz", ".gz")
# 文本附件扩展（走正则解析）
TEXT_ATTACH_EXTS = (".txt", ".log", ".csv")


def query_matches(query: str, keywords) -> bool:
    """判断 query 是否命中任一关键词（大小写不敏感）。"""
    if not query:
        return False
    q = query.lower()
    return any(kw in q for kw in keywords)
