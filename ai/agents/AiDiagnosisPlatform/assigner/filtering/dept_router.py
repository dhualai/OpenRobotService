"""Layer 1 部门路由：R5 strong → R2 LLM + R3 历史融合。"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import DeptRoutingResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.strong_kw_signal import StrongKwSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.llm_dept_signal import LlmDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.history_dept_signal import HistoryDeptSignal
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class DeptRouter:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._routing_cfg = self._config.department_routing or {}
        self._thresholds = self._routing_cfg.get("thresholds") or {}
        self._weights = self._routing_cfg.get("weights") or {}
        self._strong = StrongKwSignal(config=self._config)
        self._llm = LlmDeptSignal(config=self._config)
        self._history = HistoryDeptSignal(config=self._config)

    @staticmethod
    def _filter_by_dept(
        engineers: List[EngineerProfile], dept: str,
    ) -> List[EngineerProfile]:
        if not dept:
            return list(engineers)
        return [e for e in engineers if (e.department or "") == dept]

    @staticmethod
    def _fuse(
        llm_scores: Dict[str, float],
        hist_scores: Dict[str, float],
        w_llm: float,
        w_hist: float,
    ) -> Dict[str, float]:
        depts = set(llm_scores) | set(hist_scores)
        if not depts:
            return {}
        merged: Dict[str, float] = {}
        for dept in depts:
            merged[dept] = round(
                w_llm * llm_scores.get(dept, 0.0) + w_hist * hist_scores.get(dept, 0.0),
                4,
            )
        return merged

    @staticmethod
    def _top_two(scores: Dict[str, float]) -> Tuple[str, float, float]:
        if not scores:
            return "", 0.0, 0.0
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        return primary, top, top - second

    def _decide_mode(self, primary: str, score: float, margin: float) -> str:
        if not primary:
            return "no_filter"
        hard_score = float(self._thresholds.get("hard_filter_score", 0.80))
        hard_margin = float(self._thresholds.get("hard_filter_margin", 0.15))
        soft_score = float(self._thresholds.get("soft_prior_score", 0.55))
        if score >= hard_score and margin >= hard_margin:
            return "hard_filter"
        if score >= soft_score:
            return "soft_prior"
        return "no_filter"

    async def route(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Tuple[List[EngineerProfile], DeptRoutingResult]:
        ltag = f"[派单:{ticket.id}]"
        result = DeptRoutingResult()

        # ── R5 strong：单部门命中 → 直接 hard_filter ──
        strong = self._strong.match(ticket)
        result.signals["strong"] = {
            "dept": strong.dept,
            "keywords": strong.keywords,
            "ambiguous": strong.ambiguous,
            "ambiguous_depts": strong.ambiguous_depts,
        }
        if strong.dept and not strong.ambiguous:
            result.primary_dept = strong.dept
            result.confidence = 1.0
            result.margin = 1.0
            result.mode = "hard_filter"
            result.dept_scores = {strong.dept: 1.0}
            result.reasoning = f"R5-strong({strong.keywords[:3]})"
            filtered = self._filter_by_dept(engineers, strong.dept)
            logger.info(
                f"{ltag} Layer1-部门 R5-strong → {strong.dept} "
                f"{len(engineers)}→{len(filtered)}人"
            )
            return filtered or list(engineers), result

        if strong.ambiguous:
            logger.info(
                f"{ltag} Layer1-部门 R5-strong 跨部门歧义 "
                f"{strong.ambiguous_depts} → 走 R2+R3"
            )

        # ── R2 + R3 并行 ──
        llm_scores, hist_scores = await asyncio.gather(
            self._llm.classify(ticket),
            self._history.aggregate(ticket, engineers),
        )
        result.signals["llm"] = llm_scores
        result.signals["history"] = hist_scores

        w_llm = float(self._weights.get("llm", 0.50))
        w_hist = float(self._weights.get("history", 0.30))
        # 归一化权重（仅参与融合的路由）
        w_sum = w_llm + w_hist
        if w_sum <= 0:
            w_llm, w_hist = 0.5, 0.3
            w_sum = 0.8
        w_llm /= w_sum
        w_hist /= w_sum

        merged = self._fuse(llm_scores, hist_scores, w_llm, w_hist)
        result.dept_scores = merged
        primary, score, margin = self._top_two(merged)
        result.primary_dept = primary
        result.confidence = score
        result.margin = margin
        result.mode = self._decide_mode(primary, score, margin)

        if primary:
            reasons = []
            if llm_scores.get(primary):
                reasons.append(f"R2={llm_scores[primary]:.2f}")
            if hist_scores.get(primary):
                reasons.append(f"R3={hist_scores[primary]:.2f}")
            result.reasoning = f"{primary}({'+'.join(reasons)}) mode={result.mode}"
            logger.info(
                f"{ltag} Layer1-部门 融合 → {primary} conf={score:.2f} "
                f"margin={margin:.2f} mode={result.mode}"
            )
        else:
            result.reasoning = "未命中部门路由"
            logger.info(f"{ltag} Layer1-部门 未命中 → no_filter")

        if result.mode == "hard_filter" and primary:
            filtered = self._filter_by_dept(engineers, primary)
            if filtered:
                logger.info(
                    f"{ltag} Layer1-部门 hard_filter {len(engineers)}→{len(filtered)}人"
                )
                return filtered, result
            logger.warning(f"{ltag} Layer1-部门 hard_filter 无候选人，回退全量")
            result.mode = "no_filter"

        return list(engineers), result
