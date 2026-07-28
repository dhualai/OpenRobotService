"""历史工单匹配器：基于 tasks 表历史分配记录计算匹配分数"""

from typing import Dict, List, Optional, Set

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _kw(text, kdict):
    if not text:
        return set()
    tl = text.lower()
    out = set()
    for kws in kdict.values():
        for kw in kws:
            if kw.lower() in tl:
                out.add(kw)
    return out


class HistoryMatcher:
    def __init__(self, config=None):
        self._config = config or AssignerConfig()

    def match(self, ticket: TicketContext, engineers: List[EngineerProfile]):
        records = load_history_records(self._config.module_keywords)
        if not records:
            return {}
        qt = " ".join(filter(None, [ticket.title, ticket.problem_description]))
        qk = _kw(qt, self._config.module_keywords)
        hits: Dict[str, int] = {}
        totals: Dict[str, int] = {}
        for rec in records:
            eid = rec.get("engineer_id", "").strip()
            if not eid:
                continue
            totals[eid] = totals.get(eid, 0) + 1
            rk = set(rec.get("keywords", []))
            if not rk:
                dt = " ".join(filter(None, [rec.get("title", ""), rec.get("description", "")]))
                rk = _kw(dt, self._config.module_keywords)
            if qk & rk:
                hits[eid] = hits.get(eid, 0) + 1
        scores = {}
        for eid, t in totals.items():
            h = hits.get(eid, 0)
            if t > 0:
                scores[eid] = h / t
        if scores:
            mx = max(scores.values())
            if mx > 0:
                scores = {k: min(v / mx, 1.0) for k, v in scores.items()}
        return scores
