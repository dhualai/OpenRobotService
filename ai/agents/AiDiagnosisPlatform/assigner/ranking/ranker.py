"""精排评分层：三路加权 + 职级折扣"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


class Ranker:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        w = self._config.ranker_weights
        self._w_llm = w.get("llm_match", 0.30)
        self._w_semantic = w.get("semantic_match", 0.35)
        self._w_history = w.get("history_match", 0.10)
        self._penalty: Dict[int, float] = self._config.job_level_penalty

    def rank(
        self, recall_result: RecallResult,
        engineers: Optional[List[EngineerProfile]] = None,
    ) -> Dict[str, Dict[str, float]]:
        ids = set()
        ids.update(recall_result.llm_recall.keys())
        ids.update(recall_result.semantic_recall.keys())
        ids.update(recall_result.history_recall.keys())

        level_map: Dict[str, int] = {}
        eng_map: Dict[str, EngineerProfile] = {}
        dept_people: Dict[str, int] = {}
        if engineers:
            for e in engineers:
                level_map[e.id] = e.job_level
                eng_map[e.id] = e
                dept = e.department or ""
                dept_people[dept] = dept_people.get(dept, 0) + 1

        scores = {}
        for eid in ids:
            llm = recall_result.llm_recall.get(eid, 0.0)
            sem = recall_result.semantic_recall.get(eid, 0.0)
            his = recall_result.history_recall.get(eid, 0.0)

            raw = self._w_llm * llm + self._w_semantic * sem + self._w_history * his

            lv = level_map.get(eid, 1)
            dept = (eng_map.get(eid) or EngineerProfile(id=eid, name="")).department or ""
            # 部门内只有一人时，不打折（如机器人事业部只有文永翔 L2）
            only_one_in_dept = dept_people.get(dept, 0) <= 1
            if only_one_in_dept and lv > 1:
                mul = 0.90  # 轻微折扣，但不被其他部门的 L1 淹没
            else:
                mul = self._penalty.get(lv, self._penalty.get(99, 0.6))

            scores[eid] = {
                "llm_score": llm, "semantic_score": sem,
                "history_score": his,
                "raw_total": round(raw, 4), "job_level": lv,
                "level_multiplier": mul, "total_score": round(raw * mul, 4),
            }
        return dict(sorted(scores.items(), key=lambda x: x[1]["total_score"], reverse=True))
