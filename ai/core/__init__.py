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
from ai.core.vision_chat import (
    CHAT_UPLOAD_VISION_SYSTEM,
    build_chat_upload_vision_user_prompt,
    compare_chat_vlm_with_atlas,
    format_atlas_block_for_chat,
    strip_ungrounded_meanings,
)
from ai.core.user_profile import (
    resolve_user_profile,
    format_user_profile_block,
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
