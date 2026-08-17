"""Layer 3 模块收紧：工单命中模块锚 → 候选人须负责对应模块。"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import (
    ModuleRoutingResult,
    ProductRoutingResult,
)
from ai.agents.AiDiagnosisPlatform.assigner.filtering.product_keys import (
    classify_map_for_product,
    engineer_modules_for_product,
    profile_keys_for_product,
)
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class ModuleRouter:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._module_keywords: Dict[str, list] = self._config.module_keywords or {}
        self._module_classify: Dict[str, Dict[str, str]] = self._config.module_classify or {}

    @staticmethod
    def _ticket_text(ticket: TicketContext) -> str:
        parts = [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.fault_code or "",
            ticket.robot_type or "",
        ]
        if ticket.diagnosis_hypotheses:
            parts.extend(ticket.diagnosis_hypotheses[:5])
        return " ".join(parts).lower()

    def _match_module_keys(self, text: str, product: str = "") -> List[str]:
        """从 module_keywords 匹配「产品-类别」锚 key。"""
        hits: List[str] = []
        prefixes = profile_keys_for_product(self._config, product) if product else [""]
        for key, kws in self._module_keywords.items():
            if product and not any(key.startswith(f"{p}-") for p in prefixes):
                continue
            for kw in kws or []:
                if kw and kw.lower() in text:
                    hits.append(key)
                    break
        return hits

    @staticmethod
    def _parse_anchor_key(key: str) -> Tuple[str, str]:
        """调度USP-算法 → (调度USP, 算法)"""
        if "-" not in key:
            return key, ""
        prod, cat = key.split("-", 1)
        return prod.strip(), cat.strip()

    def _engineer_matches_anchor(
        self,
        eng: EngineerProfile,
        canonical_product: str,
        category: str,
    ) -> bool:
        mods = engineer_modules_for_product(eng, canonical_product, self._config)
        if not mods:
            return False
        cat_map = classify_map_for_product(self._config, canonical_product)
        for mod in mods:
            mapped = cat_map.get(mod)
            if mapped == category:
                return True
            if mod == category:
                return True
        return False

    def route(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        product_result: ProductRoutingResult,
    ) -> Tuple[List[EngineerProfile], ModuleRoutingResult]:
        ltag = f"[派单:{ticket.id}]"
        result = ModuleRoutingResult()

        # 模块收紧依赖产品层：产品未确定时不做模块过滤
        if product_result.mode != "hard_filter" or not product_result.product:
            result.mode = "no_filter"
            result.reasoning = "产品未确定，跳过模块收紧"
            return list(engineers), result

        text = self._ticket_text(ticket)
        product = product_result.product
        matched_keys = self._match_module_keys(text, product=product)

        if not matched_keys:
            result.mode = "no_filter"
            result.reasoning = "未命中模块关键词，跳过模块收紧"
            return list(engineers), result

        result.matched_keys = matched_keys
        categories: Set[str] = set()
        anchors: List[Tuple[str, str]] = []
        for key in matched_keys:
            prod, cat = self._parse_anchor_key(key)
            if cat:
                categories.add(cat)
            anchors.append((prod, cat))

        result.matched_categories = sorted(categories)

        kept: List[EngineerProfile] = []
        for eng in engineers:
            for prod, cat in anchors:
                if not cat:
                    continue
                check_prod = prod or product
                if not check_prod:
                    continue
                if self._engineer_matches_anchor(eng, check_prod, cat):
                    kept.append(eng)
                    break

        if not kept:
            logger.warning(
                f"{ltag} Layer3-模块 keys={matched_keys[:5]} 无候选人，跳过模块收紧"
            )
            result.mode = "no_filter"
            result.reasoning = f"命中模块={matched_keys[:3]} 无匹配工程师"
            return list(engineers), result

        result.mode = "hard_filter"
        result.reasoning = f"模块锚={matched_keys[:5]}"
        if len(kept) < len(engineers):
            logger.info(
                f"{ltag} Layer3-模块 {len(engineers)}→{len(kept)}人 "
                f"keys={matched_keys[:5]}"
            )
        return kept, result
