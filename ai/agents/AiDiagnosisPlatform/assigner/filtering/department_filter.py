"""部门过滤器：工单 → 部门

三大类问题 → 三个部门，分不清的不分。

  机器人事业部 — 车体硬件/机械故障
  车端软件     — 传感器/算法/通信协议
  智能规划     — 调度系统/服务号

分不清的不分：让三个部门的人一起参与召回+LLM决定。

关键词配置在 config.yaml 的 department_keywords 字段。
"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class DepartmentFilter:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        # 部门 → 关键词列表（从 config.yaml 加载）
        self._dept_keywords: Dict[str, List[str]] = self._config.department_keywords or {}

    def match_department(self, ticket: TicketContext) -> str:
        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
        ])).lower()

        # 按部门统计命中关键词
        hits_by_dept: Dict[str, List[str]] = {}
        for dept, keywords in self._dept_keywords.items():
            hits = [kw for kw in keywords if kw.lower() in text]
            if hits:
                hits_by_dept[dept] = hits

        # 跨部门歧义（同时命中 ≥2 个部门）→ 不过滤
        if len(hits_by_dept) >= 2:
            details = ", ".join(
                f"{d}={h[:2]}" for d, h in hits_by_dept.items()
            )
            logger.info(f"[dept_filter] 跨部门歧义 → 不过滤 ({details})")
            return ""

        if hits_by_dept:
            dept = next(iter(hits_by_dept))
            logger.info(f"[dept_filter] {dept}({hits_by_dept[dept][:3]})")
            return dept

        logger.info(f"[dept_filter] 无匹配 → 不过滤")
        return ""

    def filter_by_department(self, engineers, department):
        if not department:
            return list(engineers)
        filtered = [e for e in engineers if e.department == department]
        logger.info(f"[dept_filter] 部门过滤: {len(engineers)}→{len(filtered)} ({department})")
        return filtered

    def filter(self, ticket, engineers, project_name=""):
        if not engineers:
            return []
        dept = self.match_department(ticket)
        if dept:
            return self.filter_by_department(engineers, dept)
        return list(engineers)
