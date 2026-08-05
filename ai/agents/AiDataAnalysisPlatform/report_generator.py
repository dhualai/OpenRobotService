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
    ReportSection,
    ProjectStats,
    RiskStats,
    TicketStats,
    CollectedData,
)

logger = get_logger("ReportGenerator")


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

    project_codes 为 None 时不过滤（全局统计），为非空列表时按项目代码过滤。
    """

    def __init__(self, project_codes: list[str] | None = None) -> None:
        self._project_codes = project_codes

    def _get_db(self):
        return SessionLocal()

    # ── 项目数据 ──────────────────────────────────────────────

    def collect_project_data(self) -> ProjectStats:
        """查询 project 表，统计项目状态分布。"""
        db = self._get_db()
        try:
            q = db.query(ProjectDelivery)
            if self._project_codes:
                q = q.filter(ProjectDelivery.code.in_(self._project_codes))

            projects = q.all()
            total = len(projects)
            active = sum(1 for p in projects if p.status == "active")
            completed = sum(1 for p in projects if p.status in ("completed", "done", "closed"))
            on_hold = sum(1 for p in projects if p.status in ("on_hold", "paused", "suspended"))

            items = []
            for p in projects:
                items.append({
                    "code": p.code,
                    "name": p.name,
                    "status": p.status,
                    "issues": p.issues,
                    "risks": p.risks,
                    "deployment_date": p.deployment_date,
                    "deployment_version": p.deployment_version,
                    "recent_delivery_date": p.recent_delivery_date,
                    "recent_delivery_content": p.recent_delivery_content,
                    "final_delivery_date": p.final_delivery_date,
                    "task_execution_status": p.task_execution_status,
                    "contact_person": p.contact_person,
                    "category_basis": p.category_basis,
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
            if self._project_codes:
                q = q.filter(Risk.project_code.in_(self._project_codes))

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

                # 风险状态统计
                status = r.status or "未知"
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
                    "risk_code": r.risk_code,
                    "project_code": r.project_code,
                    "project_name": r.project_name,
                    "risk_category": r.risk_category,
                    "risk_level": r.risk_level,
                    "description": (r.description or "")[:100],
                    "status": r.status,
                    "responsible_person": r.responsible_person,
                    "created_at": r.created_at,
                    "close_time": r.close_time,
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
        """查询 users 表，构建 user_id -> 姓名的映射（姓名为空时降级为 username）。"""
        try:
            return {
                str(u.id): (u.name or u.username)
                for u in db.query(User).all()
            }
        except Exception as exc:  # 用户表不可用时不阻断报告生成
            logger.warning("查询用户表失败，接收人将以 ID 展示: %s", exc)
            return {}

    def collect_ticket_data(
        self, start: datetime, end: datetime
    ) -> TicketStats:
        """查询 tasks 表（工单表），统计指定时间范围内的工单数据。"""
        db = self._get_db()
        try:
            q = db.query(Task)
            if self._project_codes:
                q = q.filter(Task.project_id.in_(self._project_codes))

            all_tickets = q.all()
            total = len(all_tickets)

            # 用户映射：将 assigned_to 解析为真实姓名
            user_name_map = self._get_user_name_map(db)

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
                # 状态统计
                status = t.status or "unknown"
                by_status[status] = by_status.get(status, 0) + 1

                # 优先级统计
                priority = t.priority or "medium"
                by_priority[priority] = by_priority.get(priority, 0) + 1

                # 类型统计
                ttype = t.task_type or "other"
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
                    if status not in ("resolved", "closed", "canceled"):
                        overdue += 1

                # 采集当日有变更的工单明细（最多50条）
                updated = t.updated_at
                if (is_new or (updated and start <= updated <= end)) and len(items) < 50:
                    # 接收人：assigned_to 为空视为未分配，否则解析为姓名（查不到时降级为 ID）
                    assignee_name = None
                    if t.assigned_to:
                        assignee_name = user_name_map.get(str(t.assigned_to), str(t.assigned_to))
                    items.append({
                        "id": t.id,
                        "title": t.title,
                        "description": (t.description or "")[:80],
                        "status": status,
                        "type": ttype,
                        "priority": priority,
                        "project_name": t.project_name,
                        "assigned_to": t.assigned_to,
                        "assignee_name": assignee_name,
                        "created_at": created.isoformat() if created else None,
                        "updated_at": updated.isoformat() if updated else None,
                        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
                    })

            # 工单解决率（全量口径：已解决 + 已关闭 / 总数）
            done = by_status.get("resolved", 0) + by_status.get("closed", 0)
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
        logger.info("开始采集报告数据 range=%s projects=%s", date_range_str, self._project_codes)

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
    def _resolve_project_codes_by_user(user_id: str) -> list[str]:
        """查询 user_project_roles 表，获取该用户关联的全部 project_id。

        project_id 即 project.id（与 project.code 一致），可直接用于
        ProjectDelivery.code / Risk.project_code / Task.project_id 过滤。
        """
        db = SessionLocal()
        try:
            rows = db.query(UserProjectRole).filter(
                UserProjectRole.user_id == user_id
            ).all()
            codes = [r.project_id for r in rows if r.project_id]
            logger.info("user_id=%s 关联项目 %d 个: %s", user_id, len(codes), codes)
            return codes
        finally:
            db.close()

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

        # 2. 解析项目过滤范围
        if project_code:
            project_codes: list[str] | None = [project_code]
        elif user_id:
            project_codes = self._resolve_project_codes_by_user(user_id)
        else:
            project_codes = None

        # 3. 采集数据
        collector = ReportDataCollector(project_codes=project_codes)
        collected = collector.collect_all(start, end, date_range_str)

        # 4. 序列化为 JSON 文本
        data_text = json.dumps(collected.model_dump(), ensure_ascii=False, indent=2, default=str)

        # 5. 构建 prompt
        system_prompt = build_report_system_prompt(period)
        user_prompt = build_report_user_prompt(
            data_text=data_text,
            date_range=date_range_str,
            period=period,
            project_code=project_code,
            user_id=user_id,
        )

        # 6. 调用 LLM
        logger.info("开始生成%s date_range=%s", period.value, date_range_str)
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

        # 2. 解析项目过滤范围
        if project_code:
            project_codes: list[str] | None = [project_code]
        elif user_id:
            project_codes = self._resolve_project_codes_by_user(user_id)
        else:
            project_codes = None

        # 3. 采集数据
        collector = ReportDataCollector(project_codes=project_codes)
        collected = collector.collect_all(start, end, date_range_str)

        # 4. 序列化
        data_text = json.dumps(collected.model_dump(), ensure_ascii=False, indent=2, default=str)

        # 5. 构建 prompt
        system_prompt = build_report_system_prompt(period)
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
