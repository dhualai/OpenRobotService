"""R5：strong 关键词部门匹配（唯一可信的确定性路由）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext


@dataclass
class StrongKwMatch:
    dept: str = ""
    keywords: List[str] = field(default_factory=list)
    ambiguous: bool = False
    ambiguous_depts: List[str] = field(default_factory=list)


class StrongKwSignal:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._dept_keywords: Dict[str, dict] = self._config.department_keywords or {}

    @staticmethod
    def _ticket_text(ticket: TicketContext) -> str:
        return " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.fault_code or "",
        ])).lower()

    def match(self, ticket: TicketContext) -> StrongKwMatch:
        text = self._ticket_text(ticket)
        hits: List[Tuple[str, List[str]]] = []
        for dept, levels in self._dept_keywords.items():
            strong = levels.get("strong") or []
            kw_hits = [kw for kw in strong if kw.lower() in text]
            if kw_hits:
                hits.append((dept, kw_hits))

        if len(hits) == 1:
            dept, kws = hits[0]
            return StrongKwMatch(dept=dept, keywords=kws, ambiguous=False)
        if len(hits) >= 2:
            return StrongKwMatch(
                ambiguous=True,
                ambiguous_depts=[d for d, _ in hits],
                keywords=[kw for _, kws in hits for kw in kws[:2]],
            )
        return StrongKwMatch()
