"""MemoryRecallCapability — 检索 U老师此前记下的长期记忆。"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class MemoryRecallCapability(BaseCapability):
    name = "memory_recall"
    description = (
        "检索 U老师 此前自己记下的长期记忆。"
        "适用于当前问题可能与之前约定/经验相关、而知识库或历史工单检索不到时。"
        "输入: query 检索内容, top_k(默认3)。输出: 相关记忆条目。"
    )
    tags = ["memory", "记忆", "经验", "记住", "惯例"]

    async def run(self, **kwargs) -> CapabilityResult:
        query = (kwargs.get("query") or kwargs.get("query_text") or "").strip()
        if not query:
            return CapabilityResult.failure("memory_recall 需要 query")
        try:
            top_k = int(kwargs.get("top_k") or 3)
        except (TypeError, ValueError):
            top_k = 3
        try:
            from ai.agents.AiTaskPlatform.memory import (
                format_memory_block,
                get_agent_memory_service,
            )
            svc = get_agent_memory_service()
            hits = await svc.recall(query, top_k=top_k)
            if not hits:
                return CapabilityResult(text="（暂无相关长期记忆）", meta={"count": 0}, ok=True)
            return CapabilityResult(
                text=format_memory_block(hits).strip() or "（暂无相关长期记忆）",
                meta={"count": len(hits)},
                ok=True,
            )
        except Exception as e:
            logger.warning(f"MemoryRecallCapability 失败: {e}")
            return CapabilityResult.failure(f"记忆检索失败: {type(e).__name__}")
