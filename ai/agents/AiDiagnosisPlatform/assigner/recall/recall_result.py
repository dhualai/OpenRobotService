"""召回结果容器 — Step3 三路：LLM / 相似工单 / 问题域（自动簇）。"""

from typing import Any, Dict


def empty_transfer_signals() -> Dict[str, Dict[str, Any]]:
    """L3 转派旁路占位：本版恒空，精排先削/加 L1 分再加权。"""
    return {"boosts": {}, "penalties": {}}


class RecallResult:
    def __init__(self):
        self.llm_recall: Dict[str, float] = {}         # LLM 看人卡片
        self.similar_recall: Dict[str, float] = {}     # 相似工单（A）
        self.cluster_recall: Dict[str, float] = {}     # 问题域自动簇（B）
        self.llm_reasons: Dict[str, str] = {}
        self.transfer_signals: Dict[str, Dict[str, Any]] = empty_transfer_signals()

    @property
    def history_recall(self) -> Dict[str, float]:
        """兼容旧测试：曾把 A/B 合成一路。现只指向相似工单。"""
        return self.similar_recall

    @history_recall.setter
    def history_recall(self, value: Dict[str, float]) -> None:
        self.similar_recall = value or {}
