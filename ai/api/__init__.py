# 路径: ai/api/__init__.py
"""
AI 模块 API 路由

Router：
    qa_router         — /api/ai/qa/*     诊断 Agent
    chat_router       — /api/ai/chat/*   纯 LLM 对话
    memory_router     — /api/ai/memory/* 会话记忆
    task_agent_router — /api/ai/task/*   任务 Agent
    archive_router    — /api/ai/archive/* 归档流 AI（ORS-986 初稿生成）
"""

from ai.api.router import (
    qa_router, chat_router, memory_router,
    task_agent_router, wecom_router, archive_router,
)
from ai.api.assigner import assigner_router

__all__ = [
    "qa_router", "chat_router", "memory_router",
    "task_agent_router", "wecom_router", "assigner_router",
    "archive_router",
]
