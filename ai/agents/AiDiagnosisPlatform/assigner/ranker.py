"""精排评分层：固定权重多维度评分 + 职级惩罚"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


class Ranker:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        w = self._config.ranker_weights
        self._w_module = w.get("module_match", 0.40)
        self._w_history = w.get("history_match", 0.35)
        self._w_semantic = w.get("semantic_match", 0.25)
        self._penalty: Dict[int, float] = self._config.job_level_penalty

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
    ) -> Dict[str, Dict[str, float]]:
        ids = set()
        ids.update(recall_result.module_recall.keys())
        ids.update(recall_result.external_history.keys())
        ids.update(recall_result.engineer_semantic.keys())
        ids.update(recall_result.history_semantic.keys())

        level_map: Dict[str, int] = {}
        if engineers:
            for e in engineers:
                level_map[e.id] = e.job_level

        scores = {}
        for eid in ids:
            m = recall_result.module_recall.get(eid, 0.0)
            h = recall_result.external_history.get(eid, 0.0)
            s = max(recall_result.engineer_semantic.get(eid, 0.0),
                    recall_result.history_semantic.get(eid, 0.0))
            raw = self._w_module * m + self._w_history * h + self._w_semantic * s
            lv = level_map.get(eid, 1)
            mul = self._penalty.get(lv, self._penalty.get(99, 0.6))
            scores[eid] = {
                "module_score": m, "history_score": h, "semantic_score": s,
                "raw_total": round(raw, 4), "job_level": lv,
                "level_multiplier": mul, "total_score": round(raw * mul, 4),
            }
        return dict(sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True))
