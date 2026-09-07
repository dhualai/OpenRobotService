# 路径: ai/core/__init__.py
"""
AI 共享基础层
包含所有 Agent 共用的核心服务
"""

from ai.core.llm import (
    LLMClient,
    LLMProvider,
    DeepSeekProvider,
    OpenAIProvider,
    get_llm_client,
    get_intent_client,
    close_llm_client,
)
from ai.core.embed import (
    EmbedClient,
    get_embed_client,
    close_embed_client,
)
from ai.core.retrieval import (
    RetrievalService,
    RetrievalResult,
    QdrantClientWrapper,
    get_retrieval_service,
)
from ai.core.memory import (
    MemoryManager,
    SessionMemory,
    get_memory_manager,
)
from ai.core.knowledge_worker import (
    run_knowledge_worker,
    start_knowledge_worker,
)
from ai.core.verified_backfill import (
    backfill_verified_batch,
)
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
]
