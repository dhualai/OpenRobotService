"""部门 + 产品匹配器：工单 → 部门 → 产品 → 候选人

硬过滤：跨部门不互抢。一个工单先落到部门，再在部门内选产品候选人。

- 部门匹配：关键词规则（机器人事业部 → 车端软件 → 智能规划兜底）
- 产品匹配：部门内按 product_keywords 命中 product key
- project_name 预留入口，暂不参与匹配
"""

from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger(__name__)


class DepartmentMatcher:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def match_department(self, ticket: TicketContext) -> str:
        """极保守部门匹配——只在 100% 确定时才过滤。

        确定信号:
          - 车体硬件故障（开不了机/雷达坏/车体损坏/硬件） → 机器人事业部
          - 服务号相关 → 智能规划

        其他所有情况：不过滤。让召回+LLM 在全员中选择。
        """
        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
        ])).lower()

        # 硬件: 100% 机器人事业部
        hardware_kw = ["开不了机", "雷达", "车体损坏", "车体硬件", "硬件故障",
                       "激光", "传感器硬件", "电机", "电池", "车轮", "底盘"]
        if any(kw in text for kw in hardware_kw):
            logger.info(f"[dept_matcher] 硬件信号 → 机器人事业部")
            return "机器人事业部"

        # 服务号: 100% 智能规划
        service_kw = ["服务号", "微信", "我要摇人", "工单系统", "智能问答"]
        if any(kw in text for kw in service_kw):
            logger.info(f"[dept_matcher] 服务号信号 → 智能规划")
            return "智能规划"

        # 其他: 不过滤
        logger.info(f"[dept_matcher] 无强信号，不过滤（全量参与）")
        return ""

    def filter_by_department(
        self,
        engineers: List[EngineerProfile],
        department: str,
    ) -> List[EngineerProfile]:
        """按部门过滤工程师列表。空字符串 = 不过滤。"""
        if not department:
            return list(engineers)
        filtered = [e for e in engineers if e.department == department]
        logger.info(f"[dept_matcher] 部门过滤: {len(engineers)}→{len(filtered)} ({department})")
        return filtered

    def filter(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        project_name: str = "",
    ) -> List[EngineerProfile]:
        """一站式：工单 → 部门 → 候选人。

        极保守：仅服务号→智能规划（100%确定），其余全量参与。
        project_name 预留入口，暂不参与逻辑。
        """
        if not engineers:
            return []

        dept = self.match_department(ticket)

        if dept:
            engineers = self.filter_by_department(engineers, dept)
            if not engineers:
                logger.warning(f"[dept_matcher] 部门 {dept} 无可用工程师，回退全量")
                return []

        return engineers
