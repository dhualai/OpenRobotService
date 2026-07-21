"""规则过滤层：默认只保留 level=1 的一线工程师"""

from typing import List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


class RuleFilter:
    """第一层：规则过滤

    当前已实现：
    - 层级过滤：默认只保留 level=1 的一线工程师，避免上级被兜底

    预留扩展接口：
    - 负载阈值过滤（根据工程师当前工单量）
    - 可用性状态过滤（在岗 / 休假 / 离线）
    - 紧急分级过滤（P0/P1 强制绑定特定工程师）
    - 项目绑定过滤（特定项目指定负责人）
    """

    def __init__(self, include_levels: Optional[List[int]] = None):
        """
        Args:
            include_levels: 允许参与派单的层级列表，默认 [1]（仅一线工程师）
        """
        self._include_levels = include_levels or [1]

    def filter(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        workload: Optional[dict] = None,
        availability: Optional[dict] = None,
    ) -> List[EngineerProfile]:
        """根据规则过滤工程师列表。"""
        filtered = []
        for eng in engineers:
            if eng.level not in self._include_levels:
                continue
            filtered.append(eng)
        return filtered
