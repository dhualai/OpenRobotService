"""产品过滤器：工单归属产品 → 只保留负责该产品的工程师（产品级硬过滤）

背景：不同产品下即使功能模块同名（如「前端」「算法」），负责人也完全不同。
若一名工程师只负责「调度USP」而不负责「摇人吧服务号」，即便工单里出现相似
功能描述，也不应把「摇人吧服务号」的工单派给他。

本过滤器按**产品维度**做强过滤：
  - 工单 project_name 归属某产品 → 候选工程师的 responsibility_modules 中
    必须包含该产品 key，否则直接剔除。
  - 目前覆盖：项目归属「摇人吧服务号提单」时，要求候选人负责「摇人吧服务号」产品。
  - 常规 AGV/AMR 项目（调度USP 等）不引入该硬过滤，避免误伤。

与 DepartmentFilter（部门维度）是两套正交过滤：部门看归属团队，产品看负责产品线。
"""

from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

# 「摇人吧服务号」自身的项目标识（与 ranking/llm_decision.py 保持一致）。
# 用较短串「摇人吧服务号」做包含匹配，可同时命中两种叫法：
#   - 项目名「摇人吧服务号」（实际 project_name）
#   - 兜底项目名「摇人吧服务号提单」（Leo_test）
_YAORENBA_INTAKE_PROJECT_MARKERS = (
    "摇人吧服务号",
)
# 项目标识 → 要求工程师负责的产品 key（responsibility_modules 中的键）
_YAORENBA_REQUIRED_PRODUCT = "摇人吧服务号"


class ProductFilter:
    """按工单归属产品做强过滤，只保留负责该产品的工程师。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    # ── 项目→产品 映射判定 ──
    def _required_product(self, project_name: str) -> Optional[str]:
        """根据工单项目名返回要求工程师负责的产品 key；不适用则返回 None。

        命中「摇人吧服务号」项目标识 → 要求产品「摇人吧服务号」。
        其它项目（调度USP 等常规项目）→ 返回 None（不做产品硬过滤）。
        """
        project = (project_name or "").strip()
        if not project:
            return None
        # 归一化：去掉普通/全角空格后按 marker 精确包含即可
        norm = project.replace(" ", "").replace("\u3000", "")
        for marker in _YAORENBA_INTAKE_PROJECT_MARKERS:
            if marker.replace(" ", "") in norm:
                return _YAORENBA_REQUIRED_PRODUCT
        return None

    # ── 主过滤 ──
    def filter(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        project_name: str = "",
    ) -> List[EngineerProfile]:
        """对候选工程师做产品级硬过滤。

        - project_name 归属「摇人吧服务号」→ 只保留 responsibility_modules 中含
          「摇人吧服务号」产品的工程师。
        - 其它项目 / 项目为空 → 原样返回。
        - 过滤后为空 → 回退全量（避免硬过滤导致无候选人），并记 warning。
        """
        product = self._required_product(project_name)
        if not product:
            return list(engineers)

        kept = [
            e for e in engineers
            if product in (e.responsibility_modules or {})
        ]
        if not kept:
            logger.warning(
                f"[派单:{ticket.id}] 产品过滤({product})后无候选人，回退全量"
            )
            return list(engineers)

        if len(kept) < len(engineers):
            logger.info(
                f"[派单:{ticket.id}] 产品过滤 {len(engineers)}→{len(kept)}人 "
                f"(工单项目={project_name}, 要求产品={product})"
            )
        return kept
