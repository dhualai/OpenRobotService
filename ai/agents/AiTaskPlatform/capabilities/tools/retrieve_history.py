"""RetrieveHistoryCapability — 历史工单方案检索能力（简单能力，方案甲收敛）

把 `AiTaskAgent._retrieve_task_resolutions` 的领域逻辑包装为 `BaseCapability` 子类，
让 Supervisor 可调度。产品无关：query 由 runtime_ctx 或调用方传入。

对应设计：discuss 历史工单检索收敛为可调度能力；命中 verified=confirmed 时可早停。

依赖注入：
  - `retriever`：可选，从 kwargs/runtime_ctx 传入；缺失时懒加载 get_retrieval_service()
"""

from __future__ import annotations

from typing import Any, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


def _resolve_query(kwargs: dict) -> str:
    """调度 goal → 用户原话 → 工单摘要，避免 LLM 漏填 goal 时检索空跑。"""
    q = (kwargs.get("query") or kwargs.get("query_text") or "").strip()
    if q:
        return q
    q = (kwargs.get("user_query") or "").strip()
    if q:
        return q
    ct = kwargs.get("current_task") or {}
    if isinstance(ct, dict):
        return (
            ct.get("problem_summary") or ct.get("title") or ct.get("description") or ""
        ).strip()
    return ""


class RetrieveHistoryCapability(BaseCapability):
    """历史工单方案检索：在 Qdrant task_resolutions 里找相似历史工单的解决方案。

    适用于"这个问题以前有没有遇到/怎么解决"的场景。
    输入: query(检索问题)。输出: 相似历史工单方案片段。
    """

    name = "retrieve_history"
    description = (
        "历史工单方案检索：在历史解决过的工单里，找与当前问题相似的方案。"
        "适用于用户问'以前有没有遇到过/上次怎么解决的'、或需要参考历史处理经验的问题。"
        "输入: query(要检索的问题)。输出: 相似历史工单的标题与方案。"
    )
    tags = ["history", "历史", "历史工单", "历史方案"]

    async def after_run(self, result: CapabilityResult, **kwargs: Any) -> CapabilityResult:
        """对命中 confirmed 的结果盖 verified 章（供合成/前端识别）。"""
        if not result or not result.ok:
            return result
        meta = dict(result.meta or {})
        confirmed = int(meta.get("confirmed") or 0)
        if confirmed <= 0 and not result.terminate:
            return result
        meta["verified_stamp"] = "confirmed"
        meta["verified_badge"] = True
        result.meta = meta
        return result

    async def run(self, **kwargs) -> CapabilityResult:
        query = _resolve_query(kwargs)
        if not query:
            return CapabilityResult.failure("历史工单检索需要 query 参数")

        # 优先用注入的 retriever（由调用方传），否则懒加载
        retriever = kwargs.get("retriever")
        try:
            if retriever is None:
                from ai.core import get_retrieval_service
                retriever = await get_retrieval_service()
            if not hasattr(retriever, "retrieve_task_resolutions"):
                return CapabilityResult.failure("task_resolutions collection 尚未建立")

            results = await retriever.retrieve_task_resolutions(query, top_k=3)
            if not results:
                return CapabilityResult(text="（无相似的历史工单方案）", meta={"count": 0}, ok=True)

            from ai.agents.AiTaskPlatform.retrieval import format_retrieval_results
            text = format_retrieval_results(results, "task_resolutions")
            confirmed = [
                r for r in results
                if (getattr(r, "verified", None) or "unknown") == "confirmed"
            ]
            terminate = bool(confirmed)
            if terminate:
                text = "【早停】命中已验证的历史根因，可直接采用，无需再跑日志分析。\n" + text
            bus = kwargs.get("trace_bus")
            if bus is not None:
                try:
                    bus.set_attribute("命中数", len(results))
                    bus.set_attribute("verified", "confirmed" if terminate else "unknown")
                    bus.add_event("retrieve", count=len(results), confirmed=len(confirmed))
                except Exception:
                    pass
            return CapabilityResult(
                text=text,
                meta={"count": len(results), "confirmed": len(confirmed)},
                ok=True,
                terminate=terminate,
            )
        except Exception as e:
            logger.warning(f"RetrieveHistoryCapability 执行失败: {e}")
            return CapabilityResult.failure(f"历史工单检索失败: {type(e).__name__}: {e}")
