"""RetrieveTroubleshootingCapability — 排查树结论检索能力

把 `_retrieve_troubleshooting_conclusions` 的领域逻辑包装为 `BaseCapability`。
只取排查树结论节点（根因 + 方案）。产品无关：query 由调用方（Supervisor runtime_ctx / kwargs）传入。

对应设计（见 TASK_AGENT_TARGET_ARCH.md §6b.4）：`retrieve_troubleshooting` 能力。
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class RetrieveTroubleshootingCapability(BaseCapability):
    """排查树结论检索：按问题检索故障排查树，只取结论节点（根因+方案）。

    适用于需要从结构化排查树里找"这个症状的已知根因和对应方案"的场景。
    输入: query(问题/症状描述)。输出: 匹配的排查树结论。
    """

    name = "retrieve_troubleshooting"
    description = (
        "排查树结论检索：按症状/问题检索故障排查树，得到已知根因与对应处理方案。"
        "适用于用户描述一个故障现象、需要从标准排查路径找可能原因和解决步骤的问题。"
        "输入: query(症状/问题描述)。输出: 匹配的排查树结论（根因+方案）。"
    )
    tags = ["troubleshooting", "排查树", "知识库"]

    async def run(self, **kwargs) -> CapabilityResult:
        query = kwargs.get("query") or kwargs.get("query_text") or ""
        if not query:
            return CapabilityResult.failure("排查树检索需要 query 参数")

        retriever = kwargs.get("retriever")
        try:
            if retriever is None:
                from ai.core import get_retrieval_service
                retriever = await get_retrieval_service()

            if not hasattr(retriever, "retrieve_troubleshooting"):
                return CapabilityResult.failure("排查树检索通道未就绪")

            results = await retriever.retrieve_troubleshooting(query, top_k=3)
            if not results:
                return CapabilityResult(text="（无匹配的排查树结论）", meta={"count": 0}, ok=True)

            from ai.agents.AiTaskPlatform.retrieval import format_retrieval_results
            text = format_retrieval_results(results, "troubleshooting")
            return CapabilityResult(text=text, meta={"count": len(results)}, ok=True)
        except Exception as e:
            logger.warning(f"RetrieveTroubleshootingCapability 执行失败: {e}")
            return CapabilityResult.failure(f"排查树检索失败: {type(e).__name__}: {e}")
