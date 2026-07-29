"""L2 关键词召回：LLM 推断工单模块 → Jaccard 匹配工程师责任模块"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}       # L1 纯LLM
        self.module_recall: Dict[str, float] = {}     # L2 关键词
        self.semantic_recall: Dict[str, float] = {}   # L3 语义


class MultiPathRecaller:
    """L2 关键词召回：LLM 推断 → Jaccard 匹配"""

    def __init__(self, module_inferencer=None, config=None):
        self._module_inferencer = module_inferencer or ModuleInferencer(config=config)
        self._config = config or AssignerConfig()

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> RecallResult:
        result = RecallResult()
        text = " ".join(filter(None, [ticket.title, ticket.problem_description]))
        inferred = await self._module_inferencer.ainfer(text, ticket.robot_type)
        for eng in engineers:
            s = self._jaccard(inferred, eng.all_modules())
            if s > 0:
                result.module_recall[eng.id] = s
        return result

    @staticmethod
    def _jaccard(a: List[str], b: List[str]) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        i = len(sa & sb)
        u = len(sa | sb)
        return i / u if u > 0 else 0.0
