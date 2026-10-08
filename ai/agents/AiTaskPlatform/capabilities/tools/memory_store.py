"""MemoryStoreCapability — 把用户明确要记的内容写入 U老师长期记忆。"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class MemoryStoreCapability(BaseCapability):
    name = "memory_store"
    description = (
        "把一条重要信息存入 U老师 的长期记忆。"
        "仅在用户明确说「记住/记一下/以后都用」时使用，不要自行从讨论里抽取。"
        "输入: content 记忆内容。输出: 已记住的确认文本。"
    )
    tags = ["memory", "记忆", "记住", "经验", "约定"]

    async def run(self, **kwargs) -> CapabilityResult:
        content = (kwargs.get("content") or "").strip()
        if not content:
            return CapabilityResult.failure("memory_store 需要 content")
        runtime_ctx = kwargs.get("runtime_ctx") or {}
        source_id = str(
            kwargs.get("source_id")
            or runtime_ctx.get("task_id")
            or (runtime_ctx.get("current_task") or {}).get("task_id")
            or ""
        )
        try:
            from ai.agents.AiTaskPlatform.memory import get_agent_memory_service
            svc = get_agent_memory_service()
            mem_id = await svc.store(
                content=content,
                kind=kwargs.get("kind") or "directive",
                importance=float(kwargs.get("importance") or 0.8),
                source="task",
                source_id=source_id,
            )
            if not mem_id:
                return CapabilityResult.failure("记忆写入失败")
            text = f"已记住：{content}"
            return CapabilityResult(text=text, meta={"mem_id": mem_id}, ok=True)
        except Exception as e:
            logger.warning(f"MemoryStoreCapability 失败: {e}")
            return CapabilityResult.failure(f"记忆写入失败: {type(e).__name__}")
