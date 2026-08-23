"""AI 数据分析平台 · 日报/周报生成器

从 MySQL 采集项目、风险、工单数据（工单数据源为 tasks 表），调用 LLM 生成结构化日报/周报。

用法::

    # 手动调用（API 或脚本）
    from ai.agents.AiDataAnalysisPlatform.report_generator import generate_report

    result = await generate_report(period="daily", date="2026-07-20")

    # 定时任务调用（APScheduler / cron）
    result = await generate_report(period="weekly", date="2026-07-20")
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import AsyncIterator

from sqlalchemy import func as sa_func

from ai.core.database import SessionLocal, Task, ProjectDelivery, Risk, User, UserProjectRole

from .llm_client import LLMClient
from .config import AnalysisConfig
from .logging_config import get_logger
from .report_prompts import build_report_system_prompt, build_report_user_prompt
from .report_schemas import (
    ReportPeriod,
    ReportRequest,
    ReportResult,
    ReportScope,
    ReportSection,
    ProjectStats,
    RiskStats,
    TicketStats,
    CollectedData,
)

logger = get_logger("ReportGenerator")


# ── 枚举值中文化 ──────────────────────────────────────────────────
# 数据库存储的英文枚举值在采集阶段统一转换为中文标签，
# 避免 LLM 在报告正文中透出 IN_PROGRESS / CLOSED 等原始枚举。

_TICKET_STATUS_CN = {
    "new": "新建",
    "in_progress": "处理中",
    "pending": "待处理",
    "resolved": "已解决",
    "canceled": "已取消",
    "cancelled": "已取消",
    "closed": "已关闭",
}

_TICKET_PRIORITY_CN = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "urgent": "紧急",
}

_TICKET_TYPE_CN = {
    "problem": "报障",
    "bug": "Bug",
    "support": "支持需求",
    "feature": "功能需求",
    "other": "其他",
}

_PROJECT_STATUS_CN = {
    "active": "进行中",
    "completed": "已完成",
    "done": "已完成",
    "closed": "已关闭",
    "on_hold": "已暂停",
    "paused": "已暂停",
    "suspended": "已暂停",
}

_RISK_STATUS_CN = {
    "open": "未关闭",
    "opened": "未关闭",
    "closed": "已关闭",
}


def _norm_enum(value: str | None) -> str:
    """归一化枚举值：去空白、去 "TicketStatus." 类前缀、转小写。

    兼容数据库中大小写不一或带枚举类前缀的存储格式。
    """
    if not value:
        return ""
    v = str(value).strip()
    if "." in v:
        v = v.rsplit(".", 1)[-1]
    return v.lower()


def _cn_label(mapping: dict[str, str], value: str | None, default: str) -> str:
    """将枚举值转换为中文标签；未收录的值（含已是中文）原样返回。"""
    key = _norm_enum(value)
    if not key:
        return default
    return mapping.get(key, str(value).strip())


# ── 日期工具 ──────────────────────────────────────────────────────

def _parse_date(date_str: str | None) -> date:
    """解析日期字符串，为空时返回今天。"""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"日期格式错误: '{date_str}'，应为 YYYY-MM-DD")


def _get_daily_range(target: date) -> tuple[datetime, datetime]:
    """返回指定日期的 00:00 ~ 23:59:59。"""
    start = datetime.combine(target, datetime.min.time())
    end = datetime.combine(target, datetime.max.time())
    return start, end


def _get_weekly_range(target: date) -> tuple[datetime, datetime]:
    """返回 target 所在周（周一~周日）的 00:00 ~ 23:59:59。"""
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    start = datetime.combine(monday, datetime.min.time())
    end = datetime.combine(sunday, datetime.max.time())
    return start, end


# ── 数据采集 ──────────────────────────────────────────────────────

class ReportDataCollector:
    """从 MySQL 采集报告所需的统计数据。

    project_ids 为 None 时不过滤（全局统计），为非空列表时按项目ID过滤。

    表关联逻辑：
        - project.id ↔ user_project_roles.project_id（项目成员）
        - user_project_roles.user_id ↔ users.id（成员姓名展示，取 users.name）
        - project.id ↔ tasks.project_id（项目工单）
    """

    def __init__(self, project_ids: list[str] | None = None) -> None:
        self._project_ids = project_ids

    def _get_db(self):
        return SessionLocal()

    def _get_project_members_map(
        self, db, project_ids: list[str]
    ) -> dict[str, list[str]]:
        """按项目聚合成员姓名。

        关联逻辑：project.id ↔ user_project_roles.project_id，再经
        user_project_roles.user_id ↔ users.id 取 users.name 作为显示
        （姓名为空时降级为 username）。
        """
        if not project_ids:
            return {}
        try:
            rows = (
                db.query(UserProjectRole.project_id, User.name, User.username)
                .join(User, User.id == UserProjectRole.user_id)
                .filter(UserProjectRole.project_id.in_(project_ids))
                .all()
            )
            members: dict[str, list[str]] = {}
            for project_id, name, username in rows:
                members.setdefault(project_id, []).append(name or username)
            return members
        except Exception as exc:  # 关联表不可用时不阻断报告生成
            logger.warning("查询项目成员失败，项目将以空成员列表展示: %s", exc)
            return {}

    # ── 项目数据 ──────────────────────────────────────────────

    def collect_project_data(self) -> ProjectStats:
        """查询 project 表，统计项目状态分布。

        过滤关联：project.id ↔ user_project_roles.project_id（过滤列表
        来自 user_project_roles 解析出的 project_id，即 project.id）。
        """
        db = self._get_db()
        try:
            q = db.query(ProjectDelivery)
            if self._project_ids:
                q = q.filter(ProjectDelivery.id.in_(self._project_ids))

            projects = q.all()
            total = len(projects)
            active = sum(1 for p in projects if _norm_enum(p.status) == "active")
            completed = sum(1 for p in projects if _norm_enum(p.status) in ("completed", "done", "closed"))
            on_hold = sum(1 for p in projects if _norm_enum(p.status) in ("on_hold", "paused", "suspended"))

            # 项目成员：project.id ↔ user_project_roles.project_id ↔ users.id
            members_by_project = self._get_project_members_map(
                db, [p.id for p in projects if p.id]
            )

            items = []
            for p in projects:
                items.append({
                    "项目ID": p.id,
                    "项目代码": p.code,
                    "项目名称": p.name,
                    "状态": _cn_label(_PROJECT_STATUS_CN, p.status, "未知"),
                    "问题数": p.issues,
                    "风险数": p.risks,
                    "部署时间": p.deployment_date,
                    "部署版本": p.deployment_version,
                    "近期交付时间": p.recent_delivery_date,
                    "近期交付内容": p.recent_delivery_content,
                    "最终交付时间": p.final_delivery_date,
                    "任务执行情况": p.task_execution_status,
                    "对接人": p.contact_person,
                    "分类依据": p.category_basis,
                    "成员": members_by_project.get(p.id, []),
                })

            return ProjectStats(
                total=total,
                active=active,
                completed=completed,
                on_hold=on_hold,
                items=items,
            )
        finally:
            db.close()

    # ── 风险数据 ──────────────────────────────────────────────

    def collect_risk_data(
        self, start: datetime, end: datetime
    ) -> RiskStats:
        """查询 risk 表，统计指定时间范围内的风险变化。"""
        db = self._get_db()
        try:
            q = db.query(Risk)
            if self._project_ids:
                # risk 表以 project_code 关联（project.id 与 code 一致）
                q = q.filter(Risk.project_code.in_(self._project_ids))

            all_risks = q.all()
            total = len(all_risks)

            # 按创建时间判断新增
            new_risks = 0
            closed_risks = 0
            by_level: dict[str, int] = {}
            by_status: dict[str, int] = {}
            items = []

            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            for r in all_risks:
                # 风险等级统计
                level = r.risk_level or "未知"
                by_level[level] = by_level.get(level, 0) + 1

                # 风险状态统计（中文化展示）
                status = _cn_label(_RISK_STATUS_CN, r.status, "未知")
                by_status[status] = by_status.get(status, 0) + 1

                # 判断新增（created_at 在时间范围内）
                created = r.created_at or ""
                if start_str <= created[:10] <= end_str:
                    new_risks += 1

                # 判断关闭（close_time 在时间范围内）
                close = r.close_time or ""
                if close and start_str <= close[:10] <= end_str:
                    closed_risks += 1

                items.append({
                    "风险代码": r.risk_code,
                    "项目代码": r.project_code,
                    "项目名称": r.project_name,
                    "风险分类": r.risk_category,
                    "风险等级": r.risk_level,
                    "风险描述": (r.description or "")[:100],
                    "状态": status,
                    "负责人": r.responsible_person,
                    "创建时间": r.created_at,
                    "关闭时间": r.close_time,
                })

            return RiskStats(
                total=total,
                new_risks=new_risks,
                closed_risks=closed_risks,
                by_level=by_level,
                by_status=by_status,
                items=items,
            )
        finally:
            db.close()

    # ── 工单数据 ──────────────────────────────────────────────

    def _get_user_name_map(self, db) -> dict[str, str]:
        """查询 users 表，构建 user_id/username -> 姓名的映射。

        关联逻辑：tasks.assigned_to ↔ users.id 取 users.name 作为显示。
        同时以 users.id 和 username 作为键，兼容 assigned_to 存储
        用户ID或用户名两种情况；姓名为空时降级为 username。
        """
        try:
            name_map: dict[str, str] = {}
            for u in db.query(User).all():
                display = u.name or u.username
                name_map[str(u.id)] = display
                if u.username:
                    name_map[str(u.username)] = display
            return name_map
        except Exception as exc:  # 用户表不可用时不阻断报告生成
            logger.warning("查询用户表失败，接收人将以 ID 展示: %s", exc)
            return {}

    def _get_project_name_map(self, db) -> dict[str, str]:
        """查询 project 表，构建 project_id -> 项目名称 的映射。

        关联逻辑：tasks.project_id ↔ project.id，据此统一工单的项目名展示。
        """
        try:
            return {
                str(p.id): p.name
                for p in db.query(ProjectDelivery.id, ProjectDelivery.name).all()
                if p.id
            }
        except Exception as exc:  # 项目表不可用时不阻断报告生成
            logger.warning("查询项目表失败，工单项目名将以 tasks 表字段展示: %s", exc)
            return {}

    def collect_ticket_data(
        self, start: datetime, end: datetime
    ) -> TicketStats:
        """查询 tasks 表（工单表），统计指定时间范围内的工单数据。

        过滤关联：project.id ↔ tasks.project_id。
        """
        db = self._get_db()
        try:
            q = db.query(Task)
            if self._project_ids:
                q = q.filter(Task.project_id.in_(self._project_ids))

            all_tickets = q.all()
            total = len(all_tickets)

            # 用户映射：将 assigned_to 解析为真实姓名
            user_name_map = self._get_user_name_map(db)
            # 项目名映射：tasks.project_id ↔ project.id，优先用 project 表名称展示
            project_name_map = self._get_project_name_map(db)

            new_tickets = 0
            resolved = 0
            closed = 0
            overdue = 0
            by_status: dict[str, int] = {}
            by_priority: dict[str, int] = {}
            by_type: dict[str, int] = {}
            items: list[dict] = []

            today = date.today()

            for t in all_tickets:
                # 状态统计（中文化展示；raw_status 保留原始值用于完结判断）
                raw_status = _norm_enum(t.status) or "unknown"
                status = _cn_label(_TICKET_STATUS_CN, t.status, "未知")
                by_status[status] = by_status.get(status, 0) + 1

                # 优先级统计
                priority = _cn_label(_TICKET_PRIORITY_CN, t.priority, "中")
                by_priority[priority] = by_priority.get(priority, 0) + 1

                # 类型统计
                ttype = _cn_label(_TICKET_TYPE_CN, t.task_type, "其他")
                by_type[ttype] = by_type.get(ttype, 0) + 1

                # 新增工单（created_at 在时间范围内）
                created = t.created_at
                is_new = False
                if created and start <= created <= end:
                    new_tickets += 1
                    is_new = True

                # 已解决 / 已关闭（resolved_at / closed_at 在时间范围内）
                if t.resolved_at and start <= t.resolved_at <= end:
                    resolved += 1
                if t.closed_at and start <= t.closed_at <= end:
                    closed += 1

                # 逾期工单（deadline_at < today 且状态未完结）
                if t.deadline_at and t.deadline_at.date() < today:
                    if raw_status not in ("resolved", "closed", "canceled", "cancelled"):
                        overdue += 1

                # 采集当日有变更的工单明细（最多50条）
                updated = t.updated_at
                if (is_new or (updated and start <= updated <= end)) and len(items) < 50:
                    # 接收人：tasks.assigned_to ↔ users.id 关联取 users.name，为空视为未分配
                    if t.assigned_to:
                        assignee = user_name_map.get(str(t.assigned_to), str(t.assigned_to))
                    else:
                        assignee = "未分配"
                    items.append({
                        "工单ID": t.id,
                        "标题": t.title,
                        "描述": (t.description or "")[:80],
                        "状态": status,
                        "类型": ttype,
                        "优先级": priority,
                        "项目名称": project_name_map.get(str(t.project_id), t.project_name) if t.project_id else t.project_name,
                        "接收人": assignee,
                        "创建时间": created.isoformat() if created else None,
                        "更新时间": updated.isoformat() if updated else None,
                        "解决时间": t.resolved_at.isoformat() if t.resolved_at else None,
                    })

            # 工单解决率（全量口径：已解决 + 已关闭 / 总数）
            done = by_status.get("已解决", 0) + by_status.get("已关闭", 0)
            resolve_rate = (done / total * 100) if total > 0 else 0.0

            return TicketStats(
                total=total,
                new_tickets=new_tickets,
                resolved=resolved,
                closed=closed,
                overdue=overdue,
                resolve_rate=round(resolve_rate, 1),
                by_status=by_status,
                by_priority=by_priority,
                by_type=by_type,
                items=items,
            )
        finally:
            db.close()

    # ── 汇总采集 ──────────────────────────────────────────────

    def collect_all(
        self, start: datetime, end: datetime, date_range_str: str
    ) -> CollectedData:
        """汇总采集所有维度数据。"""
        logger.info("开始采集报告数据 range=%s projects=%s", date_range_str, self._project_ids)

        project = self.collect_project_data()
        risk = self.collect_risk_data(start, end)
        ticket = self.collect_ticket_data(start, end)

        data = CollectedData(
            date_range=date_range_str,
            project=project,
            risk=risk,
            ticket=ticket,
        )
        logger.info(
            "数据采集完成 projects=%d risks=%d tickets=%d",
            project.total, risk.total, ticket.total,
        )
        return data


# ── 报告生成器 ─────────────────────────────────────────────────────

class ReportGenerator:
    """日报/周报生成器。

    编排数据采集 → 构建 prompt → 调用 LLM → 解析结构化结果。
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    @staticmethod
    def _resolve_project_ids_by_user(user_id: str) -> list[str]:
        """通过 users ↔ user_project_roles 关联，获取该用户关联的全部 project_id。

        关联逻辑：user_project_roles.user_id ↔ users.id；返回的 project_id
        即 project.id（与 project.code 一致），可直接用于
        ProjectDelivery.id / Risk.project_code / Task.project_id 过滤。

        兼容两种传参：users.id 或 username（前端登录态只有 username，
        按 id 关联不到时回退按 username 关联再查）。
        """
        db = SessionLocal()
        try:
            rows = (
                db.query(UserProjectRole)
                .join(User, User.id == UserProjectRole.user_id)
                .filter(User.id == user_id)
                .all()
            )
            if not rows:
                rows = (
                    db.query(UserProjectRole)
                    .join(User, User.id == UserProjectRole.user_id)
                    .filter(User.username == user_id)
                    .all()
                )
            project_ids = [r.project_id for r in rows if r.project_id]
            logger.info("user_id=%s 关联项目 %d 个: %s", user_id, len(project_ids), project_ids)
            return project_ids
        finally:
            db.close()

    @staticmethod
    def _resolve_scope(
        project_code: str | None, user_id: str | None
    ) -> ReportScope:
        """根据请求参数确定报告数据范围（决定提示词模板族）。"""
        if project_code:
            return ReportScope.SINGLE_PROJECT
        if user_id:
            return ReportScope.USER_PROJECTS
        return ReportScope.GLOBAL

    def _resolve_project_filter(
        self, project_code: str | None, user_id: str | None
    ) -> list[str] | None:
        """解析项目过滤范围。

        - project_code → 仅该项目
        - 仅 user_id → 该用户关联的全部项目；无关联项目时用占位符保证
          查出空数据，防止空列表退化为全局统计（数据越权）
        - 均不传 → None（全局统计）
        """
        if project_code:
            return [project_code]
        if user_id:
            project_ids = self._resolve_project_ids_by_user(user_id)
            return project_ids or ["__no_project__"]
        return None

    async def generate(
        self,
        period: ReportPeriod,
        target_date: date,
        project_code: str | None = None,
        user_id: str | None = None,
    ) -> ReportResult:
        """生成日报或周报。

        过滤逻辑：
        - project_code 和 user_id 同时传 → 仅查 project_code 对应项目
        - 仅 user_id → 查该用户在 user_project_roles 中的全部项目
        - 均不传 → 全局统计
        """
        # 1. 计算时间范围
        if period == ReportPeriod.DAILY:
            start, end = _get_daily_range(target_date)
            date_range_str = target_date.strftime("%Y-%m-%d")
        else:
            start, end = _get_weekly_range(target_date)
            monday = target_date - timedelta(days=target_date.weekday())
            sunday = monday + timedelta(days=6)
            date_range_str = f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"

        # 2. 确定数据范围与项目过滤
        scope = self._resolve_scope(project_code, user_id)
        project_ids = self._resolve_project_filter(project_code, user_id)

        # 3. 采集数据
        collector = ReportDataCollector(project_ids=project_ids)
        collected = collector.collect_all(start, end, date_range_str)

        # 4. 序列化为 JSON 文本
        data_text = json.dumps(collected.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str)

        # 5. 构建 prompt（系统提示词按 scope 选用单项目/多项目模板）
        system_prompt = build_report_system_prompt(period, scope)
        user_prompt = build_report_user_prompt(
            data_text=data_text,
            date_range=date_range_str,
            period=period,
            project_code=project_code,
            user_id=user_id,
        )

        # 6. 调用 LLM
        logger.info("开始生成%s date_range=%s scope=%s", period.value, date_range_str, scope.value)
        raw_response, usage = await self._llm.chat(system_prompt, user_prompt)

        # 7. 解析结果
        sections = self._parse_sections(raw_response, collected)
        summary = self._extract_summary(raw_response)

        return ReportResult(
            period=period,
            date_range=date_range_str,
            sections=sections,
            summary=summary,
            raw_response=raw_response,
            generated_at=datetime.now().isoformat(),
            project_code=project_code,
        )

    async def generate_stream(
        self,
        period: ReportPeriod,
        target_date: date,
        project_code: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[str]:
        """流式生成报告，逐 chunk 返回文本。"""
        # 1. 计算时间范围
        if period == ReportPeriod.DAILY:
            start, end = _get_daily_range(target_date)
            date_range_str = target_date.strftime("%Y-%m-%d")
        else:
            start, end = _get_weekly_range(target_date)
            monday = target_date - timedelta(days=target_date.weekday())
            sunday = monday + timedelta(days=6)
            date_range_str = f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"

        # 2. 确定数据范围与项目过滤
        scope = self._resolve_scope(project_code, user_id)
        project_ids = self._resolve_project_filter(project_code, user_id)

        # 3. 采集数据
        collector = ReportDataCollector(project_ids=project_ids)
        collected = collector.collect_all(start, end, date_range_str)

        # 4. 序列化
        data_text = json.dumps(collected.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str)

        # 5. 构建 prompt（系统提示词按 scope 选用单项目/多项目模板）
        system_prompt = build_report_system_prompt(period, scope)
        user_prompt = build_report_user_prompt(
            data_text=data_text,
            date_range=date_range_str,
            period=period,
            project_code=project_code,
            user_id=user_id,
        )

        # 6. 流式调用 LLM
        async for chunk in self._llm.chat_stream(system_prompt, user_prompt):
            yield chunk

    # ── 结果解析 ──────────────────────────────────────────────

    @staticmethod
    def _parse_sections(raw: str, collected: CollectedData) -> list[ReportSection]:
        """从 LLM 回复中提取各章节。"""
        sections: list[ReportSection] = []

        # 匹配 ## 标题及内容
        pattern = r"^##\s+(.+?)\n((?:(?!^##\s).+?\n)*)"
        for match in re.finditer(pattern, raw, re.MULTILINE):
            title = match.group(1).strip()
            content = match.group(2).strip()
            if not content:
                continue

            # 附加指标数据
            metrics: dict = {}
            title_lower = title.lower()
            if "项目" in title_lower:
                metrics = {
                    "total": collected.project.total,
                    "active": collected.project.active,
                    "completed": collected.project.completed,
                    "on_hold": collected.project.on_hold,
                }
            elif "风险" in title_lower:
                metrics = {
                    "total": collected.risk.total,
                    "new": collected.risk.new_risks,
                    "closed": collected.risk.closed_risks,
                    "by_level": collected.risk.by_level,
                }
            elif "工单" in title_lower:
                metrics = {
                    "total": collected.ticket.total,
                    "new": collected.ticket.new_tickets,
                    "resolved": collected.ticket.resolved,
                    "closed": collected.ticket.closed,
                    "overdue": collected.ticket.overdue,
                    "resolve_rate": collected.ticket.resolve_rate,
                    "by_status": collected.ticket.by_status,
                    "by_type": collected.ticket.by_type,
                }

            sections.append(ReportSection(title=title, content=content, metrics=metrics))

        return sections

    @staticmethod
    def _extract_summary(raw: str) -> str:
        """提取摘要段落。"""
        # 尝试匹配 **摘要** 或 ## 摘要
        patterns = [
            r"\*\*摘要\*\*[：:\s]*(.+?)(?:\n\n|$)",
            r"##\s*摘要[：:\s]*(.+?)(?:\n\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                return match.group(1).strip()

        # 兜底：取最后一段非空文本
        lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
        if lines:
            return lines[-1]
        return ""


# ── 顶层入口（供定时任务调用）─────────────────────────────────────

async def generate_report(
    period: str = "daily",
    date: str | None = None,
    project_code: str | None = None,
    user_id: str | None = None,
) -> ReportResult:
    """顶层报告生成入口。

    设计为无 HTTP 依赖的纯 async 函数，可直接被
    APScheduler / Celery Beat / cron 脚本调用。

    Args:
        period: "daily" 或 "weekly"
        date: 目标日期 YYYY-MM-DD，默认今天
        project_code: 项目代码过滤（可选，与 user_id 同时传时以 project_code 为准）
        user_id: 用户ID，用于查询该用户关联的全部项目（可选）

    Returns:
        ReportResult 结构化报告结果
    """
    from .config import AnalysisConfig

    report_period = ReportPeriod(period)
    target = _parse_date(date)

    config = AnalysisConfig.from_env()
    llm = LLMClient(config)
    generator = ReportGenerator(llm)

    return await generator.generate(
        period=report_period,
        target_date=target,
        project_code=project_code,
        user_id=user_id,
    )


async def generate_report_stream(
    period: str = "daily",
    date: str | None = None,
    project_code: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    """顶层流式报告生成入口。"""
    from .config import AnalysisConfig

    report_period = ReportPeriod(period)
    target = _parse_date(date)

    config = AnalysisConfig.from_env()
    llm = LLMClient(config)
    generator = ReportGenerator(llm)

    async for chunk in generator.generate_stream(
        period=report_period,
        target_date=target,
        project_code=project_code,
        user_id=user_id,
    ):
        yield chunk
