"""精排评分层：固定权重多维度评分 + 职级惩罚"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


class Ranker:
    """第三层：精排评分（模块 + 历史 + 语义 + 职级惩罚）

    职级越高越不优先接单：对 job_level > 1 的工程师打折扣。
    不打硬性过滤——某部门无一线人员时，上级依然能被派到。
    """

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        weights = self._config.ranker_weights
        self._w_module = weights.get("module_match", 0.40)
        self._w_history = weights.get("history_match", 0.35)
        self._w_semantic = weights.get("semantic_match", 0.25)
        self._job_level_penalty: Dict[int, float] = self._config.job_level_penalty

    def rank(
        self,
        recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """对召回结果进行精排评分，含职级折扣。"""
        all_ids = set()
        all_ids.update(recall_result.module_recall.keys())
        all_ids.update(recall_result.external_history.keys())
        all_ids.update(recall_result.engineer_semantic.keys())
        all_ids.update(recall_result.history_semantic.keys())

        # 构建 engineer id → job_level 查找表
        level_map: Dict[str, int] = {}
        if engineers:
            for eng in engineers:
                level_map[eng.id] = eng.job_level

        scores = {}
        for eid in all_ids:
            module_score = recall_result.module_recall.get(eid, 0.0)
            history_score = recall_result.external_history.get(eid, 0.0)
            semantic_score = max(
                recall_result.engineer_semantic.get(eid, 0.0),
                recall_result.history_semantic.get(eid, 0.0),
            )

            raw_total = (
                self._w_module * module_score
                + self._w_history * history_score
                + self._w_semantic * semantic_score
            )

            # 职级折扣：level 1 无折扣，level 越高乘系数越低
            job_level = level_map.get(eid, 1)
            multiplier = self._job_level_penalty.get(job_level, self._job_level_penalty.get(99, 0.7))
            total = raw_total * multiplier

            scores[eid] = {
                "module_score": module_score,
                "history_score": history_score,
                "semantic_score": semantic_score,
                "raw_total": round(raw_total, 4),
                "job_level": job_level,
                "level_multiplier": multiplier,
                "total_score": round(total, 4),
            }

        sorted_scores = dict(
            sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True)
        )
        return sorted_scores
