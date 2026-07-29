"""召回结果容器 — L1/L3/L4 三路召回统一得分结构"""

from typing import Dict


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}       # L1 纯LLM (0.50)
        self.module_recall: Dict[str, float] = {}     # 已废弃,保留兼容
        self.semantic_recall: Dict[str, float] = {}   # L3 语义 (0.40)
        self.history_recall: Dict[str, float] = {}    # L4 历史 (0.10,与L3共享Embedding)
