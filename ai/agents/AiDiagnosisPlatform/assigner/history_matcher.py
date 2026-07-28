"""历史工单匹配器：基于 task_matching.json 计算历史匹配分数"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


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
    """历史工单匹配器（维度3）"""

    _DATA_DIR = Path(__file__).parent / "data"

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._records: List[dict] = self._load_records()

    def _load_records(self) -> List[dict]:
        path = self._DATA_DIR / "task_matching.json"
        if not path.exists():
            path = self._DATA_DIR / "task_matching.example.json"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def match(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """计算各工程师的历史匹配分数。"""
        if not self._records:
            return {}

        query_text = " ".join(filter(None, [ticket.title, ticket.problem_description]))
        query_keywords = _extract_keywords(query_text, {**self._config.module_keywords, **self._config.skill_keywords})

        engineer_hits: Dict[str, int] = {}
        engineer_total: Dict[str, int] = {}

        for rec in self._records:
            eng_name = rec.get("engineer_name", "")
            if not eng_name:
                continue

            eng_id = self._name_to_id(eng_name, engineers)
            if eng_id is None:
                continue

            engineer_total[eng_id] = engineer_total.get(eng_id, 0) + 1

            rec_keywords = set(rec.get("keywords", []))
            desc = rec.get("description", "")
            desc_keywords = _extract_keywords(desc, {**self._config.module_keywords, **self._config.skill_keywords})

            keyword_hit = bool(query_keywords & rec_keywords)
            desc_hit = bool(query_keywords & desc_keywords)

            if keyword_hit or desc_hit:
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

    def _name_to_id(self, name: str, engineers: List[EngineerProfile]) -> Optional[str]:
        """根据姓名查找 engineer_id。"""
        for eng in engineers:
            if eng.name == name:
                return eng.id
        return None
