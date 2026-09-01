"""候选池收紧编排：部门 → 产品。

收紧原则：只做「部门 → 产品」两层资格硬约束。
- 部门：判定工单所属部门并据此过滤候选人（核心护栏）。
- 产品：项目/部门→产品 硬过滤（如车端工单映射到车端软件，不误入调度USP）。
- 模块层已从收紧中移除：按关键字/锚做模块匹配对负责非功能模块的候选人
  （如产品经理）结构化不公平，且会把候选池收得过窄，"选谁"应交由召回 + 精排 + LLM 决策。
"""

from __future__ import annotations

from typing import List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import TightenResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
from ai.agents.AiDiagnosisPlatform.assigner.filtering.product_router import ProductRouter
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class CandidateTightener:
    """按 部门 → 产品 逐层收紧候选人池（模块层已移除）。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._dept_router = DeptRouter(config=self._config)
        self._product_router = ProductRouter(config=self._config)

    async def tighten(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> TightenResult:
        ltag = f"[派单:{ticket.id}]"
        before = len(engineers)

        # Layer 1: 部门
        candidates, dept_result = await self._dept_router.route(ticket, engineers)

        # Layer 2: 产品（传入部门判定，便于车端类工单按部门映射到对应产品，而非默认调度USP）
        candidates, product_result = self._product_router.route(
            ticket, candidates, primary_dept=dept_result.primary_dept,
        )

        after = len(candidates)
        logger.info(
            f"{ltag} 候选收紧 {before}→{after} | "
            f"部门={dept_result.mode}({dept_result.primary_dept or '-'}) | "
            f"产品={product_result.mode}({product_result.product or '-'}) | "
            f"模块层=已移除(不收紧)"
        )

        return TightenResult(
            candidates=candidates,
            before_count=before,
            after_count=after,
            dept=dept_result,
            product=product_result,
        )
