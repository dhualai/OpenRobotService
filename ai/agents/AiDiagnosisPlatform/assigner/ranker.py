"""精排评分层：固定权重多维度评分"""

from typing import Dict, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall import RecallResult


class Ranker:
    """第三层：精排评分（支持关键词 + 语义多维度）"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        weights = self._config.ranker_weights
        self._w_skill = weights.get("skill_match", 0.30)
        self._w_module = weights.get("module_match", 0.30)
        self._w_history = weights.get("history_match", 0.25)
        self._w_semantic = weights.get("semantic_match", 0.15)

    def rank(self, recall_result: RecallResult) -> Dict[str, Dict[str, float]]:
        """对召回结果进行精排评分。"""
        all_ids = set()
        all_ids.update(recall_result.module_recall.keys())
        all_ids.update(recall_result.tag_recall.keys())
        all_ids.update(recall_result.external_history.keys())
        all_ids.update(recall_result.engineer_semantic.keys())
        all_ids.update(recall_result.history_semantic.keys())

        scores = {}
        for eid in all_ids:
            module_score = recall_result.module_recall.get(eid, 0.0)
            history_score = recall_result.external_history.get(eid, 0.0)
            skill_score = recall_result.tag_recall.get(eid, 0.0)
            semantic_score = max(
                recall_result.engineer_semantic.get(eid, 0.0),
                recall_result.history_semantic.get(eid, 0.0),
            )

            total = (
                self._w_skill * skill_score
                + self._w_module * module_score
                + self._w_history * history_score
                + self._w_semantic * semantic_score
            )

            scores[eid] = {
                "skill_score": skill_score,
                "module_score": module_score,
                "history_score": history_score,
                "semantic_score": semantic_score,
                "total_score": total,
            }

        sorted_scores = dict(
            sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True)
        )
        return sorted_scores
