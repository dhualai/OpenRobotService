"""RetrieveKbCapability — 完整知识库检索能力

把「完整知识库三路检索（team 操作手册/FAQ/排查、company 产品/车端错误码/VDA5050、
industry 行业标准/导航规范）」包装为 `BaseCapability`，供 Supervisor 调度。

复用 `RetrievalService.retrieve_ai_kb`（该实现与 AiDiagnosisPlatform 的成熟检索
策略一致：三路域双路检索 + 平衡选取 + 同节去重 + 车端错误码精确匹配 + 格式化）。

支持两种触发形态：
  - Supervisor 派的子任务：kwargs 传 query（调度 LLM 组织的检索词）
  - 纯知识问答兜底：discuss_flow 在无附件、非历史场景直接调用本能力并注入 facultative

依赖注入：
  - `retriever`：可选，从 kwargs/runtime_ctx 传入；缺失时懒加载 get_retrieval_service()
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class RetrieveKbCapability(BaseCapability):
    """完整知识库检索：操作手册 / FAQ / 排查 / 车端错误码 / 产品目录 / 行业标准。

    适用于知识问答（怎么操作、错误码含义、协议/标准、产品/车型介绍、故障排查方法）场景，
    无论工单有没有附件，只要问题是需要知识库支撑的知识问答，就应派发本能力。
    输入: query(检索问题/症状/关键词)。输出: 匹配的知识库 chunk（含来源标签）。
    """

    name = "retrieve_kb"
    description = (
        "完整知识库检索：检索 AGV/AMR 操作手册、FAQ、故障排查、车端错误码、"
        "产品目录、VDA5050 协议、行业标准与导航规范等（team/company/industry 三域）。\n"
        "适用于用户问「怎么操作/怎么配置/这个流程/错误码含义/这个型号/协议标准/故障排查方法」等"
        "知识问答类问题——即使工单没有附件，也应派发本能力获取知识库依据。\n"
        "输入: query(检索问题/症状/关键词，用知识库会用的术语)。输出: 匹配的知识库 chunk。"
    )
    tags = ["knowledge", "kb", "知识库", "手册", "FAQ", "错误码", "操作手册"]

    async def run(self, **kwargs) -> CapabilityResult:
        query = kwargs.get("query") or kwargs.get("query_text") or ""
        if not query:
            return CapabilityResult.failure("知识库检索需要 query 参数")

        retriever = kwargs.get("retriever")
        try:
            if retriever is None:
                from ai.core import get_retrieval_service
                retriever = await get_retrieval_service()
            if not hasattr(retriever, "retrieve_ai_kb"):
                return CapabilityResult.failure("完整知识库检索通道未就绪")

            text = await retriever.retrieve_ai_kb(query, top_k=6)
            # 无命中时 retrieve_ai_kb 已返回友好提示文案，仍视为 ok（不抛错，供 LLM 判断）
            return CapabilityResult(text=text, meta={"count": text.count("---") // 2}, ok=True)
        except Exception as e:
            logger.warning(f"RetrieveKbCapability 执行失败: {e}")
            return CapabilityResult.failure(f"知识库检索失败: {type(e).__name__}: {e}")
