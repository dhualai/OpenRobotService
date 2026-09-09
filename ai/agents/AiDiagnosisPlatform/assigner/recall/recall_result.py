"""召回结果容器 — Step3 三路：画像召回 / 相似工单 / 问题簇。"""

from typing import Dict


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}         # LLM 看人卡片
        self.similar_recall: Dict[str, float] = {}     # 相似工单（近邻办案人）
        self.cluster_recall: Dict[str, float] = {}     # 问题簇（类型熟手）
        self.llm_reasons: Dict[str, str] = {}
        self.misassign_confirmed: Dict[str, str] = {}
        self.misassign_rejected: Dict[str, str] = {}

    @property
    def history_recall(self) -> Dict[str, float]:
        """兼容旧测试：曾把 A/B 合成一路。现只指向相似工单。"""
        return self.similar_recall

    @history_recall.setter
    def history_recall(self, value: Dict[str, float]) -> None:
        self.similar_recall = value or {}
