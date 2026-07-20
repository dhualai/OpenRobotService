# 路径: ai/api/__init__.py
"""
AI 模块 API 路由

三个 Router：
    qa_router     — /api/ai/qa/*     诊断 Agent
    chat_router   — /api/ai/chat/*   纯 LLM 对话
    memory_router — /api/ai/memory/* 会话记忆
"""

from ai.api.router import qa_router, chat_router, memory_router, assigner_router

__all__ = ["qa_router", "chat_router", "memory_router", "assigner_router"]
