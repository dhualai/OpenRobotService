# 路径: backend/app/ai/core/__init__.py
"""
AI 共享基础层
包含所有 Agent 共用的核心服务
"""

from app.ai.core.llm import (
    LLMClient,
    LLMProvider,
    DeepSeekProvider,
    OpenAIProvider,
    get_llm_client,
    close_llm_client,
)
from app.ai.core.embed import (
    EmbedClient,
    get_embed_client,
    close_embed_client,
)
from app.ai.core.retrieval import (
    RetrievalService,
    RetrievalResult,
    QdrantClientWrapper,
    get_retrieval_service,
)
from app.ai.core.memory import (
    MemoryManager,
    SessionMemory,
    get_memory_manager,
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
]
