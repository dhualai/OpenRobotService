"""候选池三层收紧编排：部门 → 产品 → 模块。"""

from __future__ import annotations

from typing import List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import TightenResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
from ai.agents.AiDiagnosisPlatform.assigner.filtering.product_router import ProductRouter
from ai.agents.AiDiagnosisPlatform.assigner.filtering.module_router import ModuleRouter
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class CandidateTightener:
    """按 部门 → 产品 → 模块 逐层收紧候选人池。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._dept_router = DeptRouter(config=self._config)
        self._product_router = ProductRouter(config=self._config)
        self._module_router = ModuleRouter(config=self._config)

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
        pre_product = list(candidates)
        candidates, product_result = self._product_router.route(
            ticket, candidates, primary_dept=dept_result.primary_dept,
        )

        # Layer 3: 模块
        pre_module = list(candidates)
        candidates, module_result = self._module_router.route(
            ticket, candidates, product_result,
        )

        after = len(candidates)
        logger.info(
            f"{ltag} 候选收紧 {before}→{after} | "
            f"部门={dept_result.mode}({dept_result.primary_dept or '-'}) | "
            f"产品={product_result.mode}({product_result.product or '-'}) | "
            f"模块={module_result.mode}({','.join(module_result.matched_categories[:3]) or '-'})"
        )

        return TightenResult(
            candidates=candidates,
            before_count=before,
            after_count=after,
            dept=dept_result,
            product=product_result,
            module=module_result,
        )
