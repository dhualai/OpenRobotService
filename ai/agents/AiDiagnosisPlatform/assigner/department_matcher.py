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

    def match_department(self, ticket: TicketContext) -> Tuple[str, list[str]]:
        """根据工单内容匹配最佳部门。

        Returns:
            (department_name, product_keys): 部门名 + 该部门下的产品列表

        匹配顺序:
            1. 机器人事业部 — 高特异性关键词
            2. 车端软件 — 中特异性关键词
            3. 智能规划 — 兜底（调度 + 服务号全在上面）
        """
        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
            ticket.robot_type or "", ticket.fault_code or "",
        ])).lower()

        scopes = self._config.department_scopes
        if not scopes:
            return "", []

        # 按顺序匹配，命中即停止
        priority_depts = ["机器人事业部", "车端软件", "智能规划"]
        for dept in priority_depts:
            scope = scopes.get(dept)
            if not scope:
                continue
            keywords = scope.get("keywords", [])
            if any(kw.lower() in text for kw in keywords):
                products = scope.get("products", [])
                logger.info(f"[dept_matcher] 部门匹配: {dept} (关键词命中)")
                return dept, products

        # 什么都没命中 → 全量兜底
        logger.info(f"[dept_matcher] 部门匹配: 未命中任何部门，全量兜底")
        return "", []

    def match_products(self, ticket: TicketContext, department: str) -> List[str]:
        """在部门内匹配产品（基于 product_keywords）。

        Returns:
            命中的产品 key 列表（可能多个，如调度USP的工单也可能涉及服务号）。
        """
        scope = self._config.department_scopes.get(department)
        if not scope:
            return []

        candidate_products = scope.get("products", [])
        if not candidate_products:
            return []

        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
            ticket.robot_type or "", ticket.fault_code or "",
        ])).lower()

        product_kw = self._config.product_keywords
        matched = []
        for prod in candidate_products:
            keywords = product_kw.get(prod, [])
            if any(kw.lower() in text for kw in keywords):
                matched.append(prod)

        if matched:
            logger.info(f"[dept_matcher] 产品匹配: {matched}")
        else:
            # 部门内的所有产品都参与（部门匹配已确保相关性）
            matched = candidate_products
            logger.info(f"[dept_matcher] 产品匹配: 未精确命中，全产品={matched}")

        return matched

    def filter_by_department(
        self,
        engineers: List[EngineerProfile],
        department: str,
    ) -> List[EngineerProfile]:
        """按部门过滤工程师列表。"""
        if not department:
            return list(engineers)
        filtered = [e for e in engineers if e.department == department]
        logger.info(f"[dept_matcher] 部门过滤: {len(engineers)}→{len(filtered)} ({department})")
        return filtered

    def filter_by_products(
        self,
        engineers: List[EngineerProfile],
        products: List[str],
    ) -> List[EngineerProfile]:
        """只保留 responsibility_modules 中包含指定产品 key 的工程师。

        一个工程师的 responsibility_modules 是 {"调度USP": [...], "服务号": [...]}
        只要 keys 与 products 有交集就保留。
        """
        if not products:
            return list(engineers)
        product_set = set(products)
        filtered = [
            e for e in engineers
            if product_set & set(e.responsibility_modules.keys())
        ]
        logger.info(f"[dept_matcher] 产品过滤: {len(engineers)}→{len(filtered)} (products={products})")
        return filtered if filtered else list(engineers)  # 过滤光了就不过滤

    def filter(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        project_name: str = "",
    ) -> List[EngineerProfile]:
        """一站式：工单 → 部门 → 产品（关键词精准匹配） → 候选人。

        project_name 预留入口，暂不参与逻辑。
        返回空列表时，外层负责回退到全量。
        """
        if not engineers:
            return []

        dept, _ = self.match_department(ticket)

        if dept:
            engineers = self.filter_by_department(engineers, dept)

        # 用关键词精确匹配产品（而非部门全部产品），缩小范围
        if dept and engineers:
            products = self.match_products(ticket, dept)
            if products:
                engineers = self.filter_by_products(engineers, products)

        return engineers
