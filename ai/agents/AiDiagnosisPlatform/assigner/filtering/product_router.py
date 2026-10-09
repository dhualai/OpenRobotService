"""Layer 2 产品收紧：工单归属产品 → 候选人必须负责该产品。"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import ProductRoutingResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.product_keys import (
    engineer_has_product,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

YAOREMBA_PRODUCT = "摇人吧服务号"
# 现场调度/车端才会写到的事实。不单用「调度」，避免摇人吧自己的派单页面被当成现场单。
_FIELD_TEXT_MARKERS = (
    "车号", "车端", "地图", "路径规划", "锁区", "任务下发",
    "货叉", "现场任务", "雷达", "车辆",
)
_VEHICLE_ID = re.compile(r"[A-Za-z]{2,}-\d+")


def is_yaorenba_field_entry(ticket, config: Optional[AssignerConfig] = None) -> bool:
    """项目名被收成摇人吧，正文却是现场的车、地图或任务。

    这种单仍按摇人吧收候选人。调用方只加提醒，不改派。
    """
    cfg = (config or AssignerConfig()).product_routing or {}
    project = (getattr(ticket, "project_name", None) or "").replace(" ", "").replace("\u3000", "")
    marked = False
    for rule in cfg.get("projects") or []:
        product = (rule.get("product") or "").strip()
        if product != YAOREMBA_PRODUCT:
            continue
        for marker in rule.get("markers") or []:
            if marker.replace(" ", "") and marker.replace(" ", "") in project:
                marked = True
                break
    if not marked:
        return False
    if (getattr(ticket, "robot_type", None) or "").strip():
        return True
    if (getattr(ticket, "fault_code", None) or "").strip():
        return True
    text = "\n".join(
        str(getattr(ticket, name, None) or "")
        for name in ("title", "problem_description", "diagnosis_problem_summary", "location")
    )
    if _VEHICLE_ID.search(text):
        return True
    return any(marker in text for marker in _FIELD_TEXT_MARKERS)


class ProductRouter:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._cfg = self._config.product_routing or {}

    def _infer_product(self, project_name: str) -> Tuple[str, str]:
        """仅按项目名推断产品。返回 (product, source)。product 为空表示按默认规则(见 route)。"""
        project = (project_name or "").strip()
        if not project:
            return "", "skipped"

        norm = project.replace(" ", "").replace("\u3000", "")
        for rule in self._cfg.get("projects") or []:
            product = (rule.get("product") or "").strip()
            for marker in rule.get("markers") or []:
                if marker.replace(" ", "") in norm:
                    return product, "project_marker"
        return "", "no_project_marker"

    def _infer_product_by_dept(self, dept: str) -> Tuple[str, str]:
        """按部门映射推断产品。返回 (product, source)。无映射返回 ("", "")。"""
        if not dept:
            return "", ""
        mapping = self._cfg.get("dept_to_product") or {}
        product = (mapping.get(dept) or "").strip()
        if product:
            return product, "dept_to_product"
        return "", ""

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
        primary_dept: str = "",
    ) -> Tuple[List[EngineerProfile], ProductRoutingResult]:
        ltag = f"[派单:{ticket.id}]"
        result = ProductRoutingResult()

        # 1) 项目标记（最高优先级）：只认项目名
        product, source = self._infer_product(ticket.project_name or "")
        if source != "project_marker":
            # 2) 无项目标记：部门映射
            product, source = self._infer_product_by_dept(primary_dept)
            if not product:
                # 3) 兜底默认产品
                project = (ticket.project_name or "").strip()
                default_product = (self._cfg.get("default_product") or "").strip()
                if default_product and (
                    project  # 有项目且 default_when_project_set 时兜底
                    and self._cfg.get("default_when_project_set", True)
                ):
                    product, source = default_product, "default"
                else:
                    result.mode = "no_filter"
                    result.reasoning = "无项目/未推断产品，跳过产品收紧"
                    return list(engineers), result

        result.source = source
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
