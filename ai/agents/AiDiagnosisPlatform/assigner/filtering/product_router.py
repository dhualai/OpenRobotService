"""Layer 2 产品收紧：工单归属产品 → 候选人必须负责该产品。"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import ProductRoutingResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.product_keys import (
    engineer_has_product,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class ProductRouter:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._cfg = self._config.product_routing or {}

    def _infer_product(self, project_name: str) -> Tuple[str, str]:
        """返回 (product, source)。product 为空表示跳过产品收紧。"""
        project = (project_name or "").strip()
        if not project:
            return "", "skipped"

        norm = project.replace(" ", "").replace("\u3000", "")
        for rule in self._cfg.get("projects") or []:
            product = (rule.get("product") or "").strip()
            for marker in rule.get("markers") or []:
                if marker.replace(" ", "") in norm:
                    return product, "project_marker"

        default_product = (self._cfg.get("default_product") or "").strip()
        if default_product and self._cfg.get("default_when_project_set", True):
            return default_product, "default"
        return "", "skipped"

    def _filter_by_product(
        self, engineers: List[EngineerProfile], product: str,
    ) -> List[EngineerProfile]:
        if not product:
            return list(engineers)
        return [
            e for e in engineers
            if engineer_has_product(e, product, self._config)
        ]

    def route(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Tuple[List[EngineerProfile], ProductRoutingResult]:
        ltag = f"[派单:{ticket.id}]"
        result = ProductRoutingResult()
        product, source = self._infer_product(ticket.project_name or "")
        result.source = source

        if not product:
            result.mode = "no_filter"
            result.reasoning = "无项目/未推断产品，跳过产品收紧"
            return list(engineers), result

        result.product = product
        filtered = self._filter_by_product(engineers, product)
        if not filtered:
            logger.warning(
                f"{ltag} Layer2-产品({product}) 无候选人，跳过产品收紧"
            )
            result.mode = "no_filter"
            result.reasoning = f"产品={product} 无匹配候选人"
            return list(engineers), result

        result.mode = "hard_filter"
        result.reasoning = f"产品={product} source={source}"
        if len(filtered) < len(engineers):
            logger.info(
                f"{ltag} Layer2-产品 {len(engineers)}→{len(filtered)}人 "
                f"(product={product})"
            )
        return filtered, result
