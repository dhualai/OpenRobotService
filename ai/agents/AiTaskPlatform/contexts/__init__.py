"""contexts — 工单上下文与评论域。

- contexts: 从 tasks 表加载工单上下文、平台工单判断、检索查询构建
- comments: 讨论评论读取与 AI 结果落库
"""
from ai.agents.AiTaskPlatform.contexts.contexts import (
    load_task_context,
    is_platform_ticket,
    build_query,
    build_task_ctx,
    build_img_ctx,
)
from ai.agents.AiTaskPlatform.contexts.comments import (
    load_discussion,
    add_diagnosis_comment,
    add_diagnosis_comment_short,
    notify_backend_comment_broadcast,
    notify_backend_ai_progress,
    notify_backend_ai_progress_await,
)

__all__ = [
    "load_task_context", "is_platform_ticket", "build_query",
    "build_task_ctx", "build_img_ctx",
    "load_discussion", "add_diagnosis_comment",
    "add_diagnosis_comment_short", "notify_backend_comment_broadcast",
    "notify_backend_ai_progress", "notify_backend_ai_progress_await",
]
