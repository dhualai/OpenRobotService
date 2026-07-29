"""L2 关键词召回：LLM 推断工单模块 → Jaccard 匹配工程师责任模块"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.history_matcher import HistoryMatcher
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}       # L1 纯LLM
        self.module_recall: Dict[str, float] = {}     # L2 关键词(L2已关闭,保留为空)
        self.semantic_recall: Dict[str, float] = {}   # L2 Embedding语义
        self.history_recall: Dict[str, float] = {}    # L3 历史派单(0.10,数据积累中)


class MultiPathRecaller:
    """L2 关键词召回 + L3 历史召回"""

    def __init__(self, module_inferencer=None, config=None):
        self._module_inferencer = module_inferencer or ModuleInferencer(config=config)
        self._config = config or AssignerConfig()
        self._history_matcher = HistoryMatcher(config=self._config)

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> RecallResult:
        result = RecallResult()
        # L2 关键词(已关闭,权重0——保留代码备开)
        # L3 历史召回
        try:
            result.history_recall = self._history_matcher.match(
                ticket=ticket, engineers=engineers,
            )
        except Exception:
            pass
        return result

    @staticmethod
    def _jaccard(a: List[str], b: List[str]) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        i = len(sa & sb)
        u = len(sa | sb)
        return i / u if u > 0 else 0.0
