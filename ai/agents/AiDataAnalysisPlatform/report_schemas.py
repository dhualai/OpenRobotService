"""AI 数据分析平台 · 日报/周报数据模型

定义报告请求、报告结果及内部数据结构的 Pydantic schema。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── 报告周期 ──────────────────────────────────────────────────

class ReportPeriod(str, Enum):
    """报告周期类型。"""

    DAILY = "daily"
    WEEKLY = "weekly"


# ── 请求 ─────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    """日报/周报生成请求。"""

    period: ReportPeriod = Field(..., description="报告周期：daily / weekly")
    date: str | None = Field(
        default=None,
        description="指定日期 YYYY-MM-DD，默认今天（日报）或本周一（周报）",
    )
    project_code: str | None = Field(
        default=None, description="按项目代码过滤（可选，为空则全局统计）"
    )
    stream: bool = Field(default=False, description="是否流式输出")


# ── 响应 ─────────────────────────────────────────────────────

class ReportSection(BaseModel):
    """报告章节。"""

    title: str = Field(..., description="章节标题（如：项目进度、风险变化）")
    content: str = Field(..., description="章节正文内容")
    metrics: dict[str, Any] = Field(
        default_factory=dict, description="关键指标数值"
    )


class ReportResult(BaseModel):
    """完整报告结果。"""

    period: ReportPeriod
    date_range: str = Field(..., description="数据时间范围，如 2026-07-14 ~ 2026-07-20")
    sections: list[ReportSection] = Field(
        default_factory=list, description="报告各章节"
    )
    summary: str = Field("", description="LLM 生成的总体摘要")
    raw_response: str | None = Field(default=None, description="大模型原始回复")
    generated_at: str = Field(..., description="报告生成时间 ISO 格式")
    project_code: str | None = Field(default=None, description="关联项目代码")


# ── 内部采集数据 ─────────────────────────────────────────────

class ProjectStats(BaseModel):
    """项目维度统计数据。"""

    total: int = 0
    active: int = 0
    completed: int = 0
    on_hold: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


class RiskStats(BaseModel):
    """风险维度统计数据。"""

    total: int = 0
    new_risks: int = 0
    closed_risks: int = 0
    by_level: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)


class TicketStats(BaseModel):
    """工单维度统计数据（数据源：tasks 表）。"""

    total: int = 0
    new_tickets: int = 0
    resolved: int = 0
    closed: int = 0
    overdue: int = 0
    resolve_rate: float = 0.0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list, description="工单明细列表")


class CollectedData(BaseModel):
    """从 MySQL 采集的原始数据汇总。"""

    date_range: str
    project: ProjectStats = Field(default_factory=ProjectStats)
    risk: RiskStats = Field(default_factory=RiskStats)
    ticket: TicketStats = Field(default_factory=TicketStats)
