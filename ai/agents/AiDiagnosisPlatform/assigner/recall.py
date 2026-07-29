"""L2 关键词召回（当前关闭，权重0——保留代码备开）"""

from typing import Dict


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}       # L1 纯LLM
        self.module_recall: Dict[str, float] = {}     # L2 关键词(关闭)
        self.semantic_recall: Dict[str, float] = {}   # L3 语义
        self.history_recall: Dict[str, float] = {}    # L4 历史(Embedding聚合,与L3共享)


class MultiPathRecaller:
    """L2 关键词召回（当前关闭）"""
    async def arecall(self, ticket, engineers) -> RecallResult:
        return RecallResult()  # L2 关闭，返回空结果
