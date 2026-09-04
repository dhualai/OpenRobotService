"""CodeSearchCapability — 代码检索能力（简单能力，方案甲收敛）

把 `code_skill.CodeSkill.search` 包装为 `BaseCapability` 子类，让 Supervisor 可调度。
产品无关：query 由调用方传入。

对应设计（见 TASK_AGENT_TARGET_ARCH.md §6b / 方案甲）：
  - 收敛 discuss_flow 的 3c 代码检索路径
  - is_available(): 依赖 CODE_SKILL_PATHS（服务器不可用 → 返回 False，不暴露给调度）
  - 包装现有 `get_code_skill()` 单例，不破坏既有调用

注：后续可把 `CodeSkill` 本身改为直接继承 `BaseCapability`（D12 实现方式），
本类当前作为薄包装优先保证收敛。
"""

from __future__ import annotations

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")


class CodeSearchCapability(BaseCapability):
    """代码检索：搜索项目源代码，沿调用图展开上下游，定位实现/逻辑。

    适用于"这个功能怎么实现的 / 看下某段逻辑"。当前仅本地可用（服务器不放代码）。
    输入: query(检索问题)。输出: 相关代码片段与调用图说明。
    """

    name = "code_search"
    description = (
        "代码检索：搜索项目源代码，理解某功能/逻辑是怎么实现的。"
        "适用于用户问'代码层面怎么实现/为什么会这样'、或需要看源码机制的问题。"
        "输入: query(要检索的代码相关描述)。输出: 相关代码片段。"
        "注意: 该能力仅在代码索引环境可用。"
    )
    tags = ["code", "代码", "源码", "实现"]

    def is_available(self) -> bool:
        """依赖 CODE_SKILL_PATHS；服务器不放代码 → 返回 False。"""
        try:
            from ai.config import get_ai_config
            return bool((get_ai_config().code_skill_paths or "").strip())
        except Exception:
            return False

    async def run(self, **kwargs) -> CapabilityResult:
        query = kwargs.get("query") or ""
        if not query:
            return CapabilityResult.failure("代码检索需要 query 参数")

        try:
            from ai.agents.AiTaskPlatform.code_skill.skill import get_code_skill
            skill = get_code_skill()
            skill.ensure_index()
            code_result = await skill.search(query)
            text = code_result.to_prompt_text()
            if not text or "未找到" in text:
                return CapabilityResult(text="（代码检索未找到相关实现）", meta={"found": False}, ok=True)
            return CapabilityResult(
                text=f"[代码检索结果]\n{text}",
                meta={"found": True},
                ok=True,
            )
        except Exception as e:
            logger.warning(f"CodeSearchCapability 执行失败: {e}")
            return CapabilityResult.failure(f"代码检索失败: {type(e).__name__}: {e}")
