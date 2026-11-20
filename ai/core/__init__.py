# 路径: ai/core/__init__.py
"""
AI 共享基础层
包含所有 Agent 共用的核心服务

**惰性导出**：本包被大量模块 import，其中最常见的是
`from ai.core.logging import get_logger`。若在 `__init__` 顶层就把 retrieval
（→ qdrant_client）、embed、knowledge_worker 这些重依赖拉起来，那么
「只是想用 logging / user_profile」的调用方也会被迫依赖 AI 全套。

实测：后端进程（不装 AI 依赖）里 import 任何 `ai.core.*` 子模块都会炸
`No module named 'qdrant_client'`，连带开发者模式的「读取归档审核人失败」。

故改为按需导出：`from ai.core import get_retrieval_service` 仍然有效，
但 `from ai.core.logging import get_logger` 不再触发重依赖加载。

新增导出项时在 _LAZY_EXPORTS 里登记一行即可；**不要**改回顶层 import。
"""

from __future__ import annotations

import importlib
from typing import Dict

# 导出名 → 所在子模块
_LAZY_EXPORTS: Dict[str, str] = {}


def _export(module: str, *names: str) -> None:
    for n in names:
        _LAZY_EXPORTS[n] = module


_export(
    "ai.core.llm",
    "LLMClient", "LLMProvider", "DeepSeekProvider", "OpenAIProvider",
    "get_llm_client", "get_intent_client", "close_llm_client",
)
_export("ai.core.embed", "EmbedClient", "get_embed_client", "close_embed_client")
_export(
    "ai.core.retrieval",
    "RetrievalService", "RetrievalResult", "QdrantClientWrapper", "get_retrieval_service",
)
_export("ai.core.memory", "MemoryManager", "SessionMemory", "get_memory_manager")
_export("ai.core.knowledge_worker", "run_knowledge_worker", "start_knowledge_worker")
_export("ai.core.verified_backfill", "backfill_verified_batch")
_export(
    "ai.core.vision_chat",
    "CHAT_UPLOAD_VISION_SYSTEM", "build_chat_upload_vision_user_prompt",
    "compare_chat_vlm_with_atlas", "format_atlas_block_for_chat", "strip_ungrounded_meanings",
)
_export("ai.core.user_profile", "resolve_user_profile", "format_user_profile_block")


def __getattr__(name: str):
    """PEP 562 模块级惰性导出。"""
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))


__all__ = [
    # LLM
    "LLMClient",
    "LLMProvider",
    "DeepSeekProvider",
    "OpenAIProvider",
    "get_llm_client",
    "close_llm_client",
    # Embed
    "EmbedClient",
    "get_embed_client",
    "close_embed_client",
    # Retrieval
    "RetrievalService",
    "RetrievalResult",
    "QdrantClientWrapper",
    "get_retrieval_service",
    # Memory
    "MemoryManager",
    "SessionMemory",
    "get_memory_manager",
    # 知识沉淀（core 共享，任务 Agent / 派单共用）
    "run_knowledge_worker",
    "start_knowledge_worker",
    "backfill_verified_batch",
    # 开发者看图对照试验（正式上传不走这里）
    "CHAT_UPLOAD_VISION_SYSTEM",
    "build_chat_upload_vision_user_prompt",
    "format_atlas_block_for_chat",
    "compare_chat_vlm_with_atlas",
    "strip_ungrounded_meanings",
    # 用户画像（诊断 / 任务 Agent 共用）
    "resolve_user_profile",
    "format_user_profile_block",
]
