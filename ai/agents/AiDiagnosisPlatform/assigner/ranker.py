"""精排评分层：四路加权 + 职级折扣"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


class Ranker:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        w = self._config.ranker_weights
        self._w_llm = w.get("llm_match", 0.30)
        self._w_module = w.get("module_match", 0.35)
        self._w_semantic = w.get("semantic_match", 0.35)
        self._w_history = w.get("history_match", 0.10)
        self._penalty: Dict[int, float] = self._config.job_level_penalty

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
    ) -> Dict[str, Dict[str, float]]:
        ids = set()
        ids.update(recall_result.llm_recall.keys())
        ids.update(recall_result.module_recall.keys())
        ids.update(recall_result.semantic_recall.keys())
        ids.update(recall_result.history_recall.keys())

        level_map: Dict[str, int] = {}
        if engineers:
            for e in engineers:
                level_map[e.id] = e.job_level

        scores = {}
        for eid in ids:
            llm = recall_result.llm_recall.get(eid, 0.0)
            mod = recall_result.module_recall.get(eid, 0.0)
            sem = recall_result.semantic_recall.get(eid, 0.0)
            his = recall_result.history_recall.get(eid, 0.0)

            raw = self._w_llm * llm + self._w_module * mod + self._w_semantic * sem + self._w_history * his
            lv = level_map.get(eid, 1)
            mul = self._penalty.get(lv, self._penalty.get(99, 0.6))

            scores[eid] = {
                "llm_score": llm, "module_score": mod, "semantic_score": sem,
                "history_score": his,
                "raw_total": round(raw, 4), "job_level": lv,
                "level_multiplier": mul, "total_score": round(raw * mul, 4),
            }
        return dict(sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True))
