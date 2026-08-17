"""R3：历史相似工单的被派人部门分布。"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class HistoryDeptSignal:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        routing = self._config.department_routing or {}
        self._hist_cfg = routing.get("history") or {}
        self._retriever = None

    @property
    def enabled(self) -> bool:
        return bool(self._hist_cfg.get("enabled", True))

    def _build_query_text(self, ticket: TicketContext) -> str:
        return " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.robot_type or "",
            ticket.fault_code or "",
        ]))

    async def _get_retriever(self):
        if self._retriever is None:
            from ai.core import get_retrieval_service
            self._retriever = await get_retrieval_service()
        return self._retriever

    async def aggregate(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """返回 {部门名: 归一化分数}。"""
        if not self.enabled:
            return {}

        eng_dept = {e.id: (e.department or "").strip() for e in engineers if e.id}
        dept_set = {d for d in eng_dept.values() if d}
        if not dept_set:
            return {}

        q = self._build_query_text(ticket)
        if not q.strip():
            return {}

        top_k = int(self._hist_cfg.get("top_k", 10))
        min_sim = float(self._hist_cfg.get("min_similarity", 0.70))

        try:
            retriever = await self._get_retriever()
            hits = await retriever.retrieve_dispatch_history(q, top_k=top_k)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] R3-历史部门路由失败: {e}")
            return {}

        weighted: Counter = Counter()
        total_w = 0.0
        for h in hits:
            sim = float(h.get("score", 0.0))
            if sim < min_sim:
                continue
            eid = (h.get("engineer_id") or "").strip()
            dept = eng_dept.get(eid, "")
            if not dept or dept not in dept_set:
                continue
            weighted[dept] += sim
            total_w += sim

        if not weighted or total_w <= 0:
            return {}

        scores = {dept: round(cnt / total_w, 4) for dept, cnt in weighted.items()}
        logger.info(
            f"[派单:{ticket.id}] R3-历史部门 "
            + " | ".join(f"{d}={s:.2f}" for d, s in sorted(scores.items(), key=lambda x: -x[1]))
        )
        return scores
