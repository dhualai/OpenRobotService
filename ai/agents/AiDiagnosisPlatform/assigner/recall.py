"""多路召回层：模块召回 + 历史工单召回"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_matcher import HistoryMatcher
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class RecallResult:
    def __init__(self):
        self.module_recall: Dict[str, float] = {}
        self.external_history: Dict[str, float] = {}
        self.engineer_semantic: Dict[str, float] = {}
        self.history_semantic: Dict[str, float] = {}


class MultiPathRecaller:
    def __init__(
        self, module_inferencer=None, config=None,
    ):
        self._module_inferencer = module_inferencer or ModuleInferencer(config=config)
        self._config = config or AssignerConfig()
        self._history_matcher = HistoryMatcher(config=self._config)

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
        historical_matches: Optional[Dict[str, float]] = None,
    ) -> RecallResult:
        result = RecallResult()
        text = " ".join(filter(None, [ticket.title, ticket.problem_description]))
        # ① 模块召回
        inferred = await self._module_inferencer.ainfer(text, ticket.robot_type)
        for eng in engineers:
            s = self._jaccard(inferred, eng.all_modules())
            if s > 0:
                result.module_recall[eng.id] = s
        # ② 历史匹配
        if historical_matches:
            result.external_history = {
                eid: s for eid, s in historical_matches.items()
                if any(e.id == eid for e in engineers)
            }
        else:
            local = self._history_matcher.match(ticket=ticket, engineers=engineers)
            if local:
                result.external_history = local
        return result

    @staticmethod
    def _jaccard(a: List[str], b: List[str]) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        i = len(sa & sb)
        u = len(sa | sb)
        return i / u if u > 0 else 0.0
