"""多路召回层：模块召回 + 语义召回 + 标签召回 + 历史工单召回"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_matcher import HistoryMatcher
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class RecallResult:
    """多路召回结果（关键词 + 语义）"""

    def __init__(self):
        # 关键词召回
        self.module_recall: Dict[str, float] = {}
        self.tag_recall: Dict[str, float] = {}
        self.external_history: Dict[str, float] = {}
        # 语义召回（Embedding）
        self.engineer_semantic: Dict[str, float] = {}
        self.history_semantic: Dict[str, float] = {}


class MultiPathRecaller:
    """第二层：多路召回"""

    def __init__(
        self,
        module_inferencer: Optional[ModuleInferencer] = None,
        config: Optional[AssignerConfig] = None,
    ):
        self._module_inferencer = module_inferencer or ModuleInferencer(config=config)
        self._config = config or AssignerConfig()
        self._history_matcher = HistoryMatcher(config=self._config)

    async def arecall(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        historical_matches: Optional[Dict[str, float]] = None,
    ) -> RecallResult:
        """异步执行多路召回。"""
        result = RecallResult()

        inference_text = " ".join(
            filter(None, [ticket.title, ticket.problem_description])
        )

        # 1. 模块召回（异步 LLM 推断 + 规则兜底）
        inferred_modules = await self._module_inferencer.ainfer(
            inference_text, ticket.robot_type
        )
        for eng in engineers:
            score = self._module_match_score(inferred_modules, eng.responsibility_modules)
            if score > 0:
                result.module_recall[eng.id] = score

        # 2. 标签召回（基于标题+问题描述）
        tag_scores = self._tag_recall(inference_text, engineers)
        result.tag_recall = tag_scores

        # 3. 精确技能召回
        if ticket.required_skills:
            exact_scores = self._exact_skill_recall(ticket.required_skills, engineers)
            for eid, score in exact_scores.items():
                result.tag_recall[eid] = max(result.tag_recall.get(eid, 0.0), score)

        # 4. 历史匹配
        if historical_matches:
            result.external_history = {
                eid: score for eid, score in historical_matches.items()
                if any(e.id == eid for e in engineers)
            }
        else:
            local_history = self._history_matcher.match(ticket=ticket, engineers=engineers)
            if local_history:
                result.external_history = local_history

        return result

    def _module_match_score(self, inferred: List[str], engineer_modules: List[str]) -> float:
        """计算模块匹配分数：Jaccard 相似度。"""
        if not inferred or not engineer_modules:
            return 0.0
        set_a = set(inferred)
        set_b = set(engineer_modules)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _tag_recall(
        self, text: str, engineers: List[EngineerProfile]
    ) -> Dict[str, float]:
        """标签召回：从文本提取关键词 → 匹配工程师 skills。"""
        from ai.utils.keywords import extract_keywords
        query_keywords = extract_keywords(text, self._config.skill_keywords)
        scores: Dict[str, float] = {}

        for eng in engineers:
            eng_skills = set(eng.skills)
            if not eng_skills:
                continue
            overlap = len(query_keywords & eng_skills)
            if overlap > 0:
                scores[eng.id] = overlap / max(len(query_keywords), len(eng_skills))

        return scores

    def _exact_skill_recall(
        self, required_skills: List[str], engineers: List[EngineerProfile]
    ) -> Dict[str, float]:
        """精确技能召回。"""
        req_set = set(required_skills)
        scores: Dict[str, float] = {}
        for eng in engineers:
            eng_skills = set(eng.skills)
            if not eng_skills:
                continue
            overlap = req_set & eng_skills
            if overlap:
                scores[eng.id] = len(overlap) / max(len(req_set), len(eng_skills))
        return scores
