"""历史工单匹配器：基于 tasks 表历史分配记录计算匹配分数"""

from typing import Dict, List, Optional, Set

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_sync import load_history_records, invalidate_cache
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger(__name__)


def _extract_keywords(text: str, keyword_dict: Dict[str, List[str]]) -> Set[str]:
    """从文本中提取在 keyword_dict 中出现的词。"""
    if not text:
        return set()
    text_lower = text.lower()
    matched: Set[str] = set()
    for keywords in keyword_dict.values():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.add(kw)
    return matched


class HistoryMatcher:
    """历史工单匹配器（维度3）

    数据源：DB tasks 表（status=resolved/closed）→ history_sync 缓存。
    """

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def _load_records(self) -> List[dict]:
        return load_history_records(self._config.module_keywords)

    def match(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """计算各工程师的历史匹配分数。"""
        records = self._load_records()
        if not records:
            return {}

        query_text = " ".join(filter(None, [ticket.title, ticket.problem_description]))
        query_keywords = _extract_keywords(query_text, self._config.module_keywords)

        engineer_hits: Dict[str, int] = {}
        engineer_total: Dict[str, int] = {}

        for rec in records:
            eng_id = rec.get("engineer_id", "").strip()
            if not eng_id:
                continue

            engineer_total[eng_id] = engineer_total.get(eng_id, 0) + 1

            rec_keywords = set(rec.get("keywords", []))
            if not rec_keywords:
                desc_text = " ".join(filter(None, [
                    rec.get("title", ""),
                    rec.get("description", ""),
                ]))
                rec_keywords = _extract_keywords(desc_text, self._config.module_keywords)

            if query_keywords & rec_keywords:
                engineer_hits[eng_id] = engineer_hits.get(eng_id, 0) + 1

        scores = {}
        for eng_id, total in engineer_total.items():
            hits = engineer_hits.get(eng_id, 0)
            if total > 0:
                scores[eng_id] = hits / total

        if scores:
            max_score = max(scores.values())
            if max_score > 0:
                scores = {k: min(v / max_score, 1.0) for k, v in scores.items()}

        return scores
