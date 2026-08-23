"""召回结果容器 — L1/L2/L3 三路召回统一得分结构"""

from typing import Dict


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}       # L1 纯LLM (0.70)
        self.semantic_recall: Dict[str, float] = {}   # L2 语义 (0.20)
        self.history_recall: Dict[str, float] = {}    # L3 历史 (0.10)
