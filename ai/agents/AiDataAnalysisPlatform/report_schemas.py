"""AI 数据分析平台 · 日报/周报数据模型

定义报告请求、报告结果及内部数据结构的 Pydantic schema。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ── 报告周期 ──────────────────────────────────────────────────

class ReportPeriod(str, Enum):
    """报告周期类型。"""

    DAILY = "daily"
    WEEKLY = "weekly"


class ReportScope(str, Enum):
    """报告数据范围，由前端请求参数决定，据此选用不同的提示词模板。

    - SINGLE_PROJECT：前端传了 project_code，仅分析该单个项目
    - USER_PROJECTS：未传 project_code 但传了 user_id，分析该用户关联的全部项目与工单
    - GLOBAL：均未传，平台全局统计
    """

    SINGLE_PROJECT = "single_project"
    USER_PROJECTS = "user_projects"
    GLOBAL = "global"


# ── 请求 ─────────────────────────────────────────────────────

class ReportRequest(BaseModel):
    """日报/周报生成请求。"""

    period: ReportPeriod = Field(..., description="报告周期：daily / weekly")
    date: str | None = Field(
        default=None,
        description="指定日期 YYYY-MM-DD，默认今天（日报）或本周一（周报）",
    )
    project_code: str | None = Field(
        default=None, description="按项目代码过滤（可选，与 user_id 同时传时以 project_code 为准）"
    )
    user_id: str | None = Field(
        default=None,
        description="用户ID，用于查询该用户关联的全部项目（可选，为空时不按用户过滤）",
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

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(0, alias="项目总数")
    active: int = Field(0, alias="活跃项目")
    completed: int = Field(0, alias="已完成项目")
    on_hold: int = Field(0, alias="暂停项目")
    items: list[dict[str, Any]] = Field(default_factory=list, alias="项目明细")


class RiskStats(BaseModel):
    """风险维度统计数据。"""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(0, alias="风险总数")
    new_risks: int = Field(0, alias="新增风险")
    closed_risks: int = Field(0, alias="已关闭风险")
    by_level: dict[str, int] = Field(default_factory=dict, alias="按等级分布")
    by_status: dict[str, int] = Field(default_factory=dict, alias="按状态分布")
    items: list[dict[str, Any]] = Field(default_factory=list, alias="风险明细")


class TicketStats(BaseModel):
    """工单维度统计数据（数据源：tasks 表）。"""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(0, alias="工单总数")
    new_tickets: int = Field(0, alias="新增工单")
    resolved: int = Field(0, alias="已解决")
    closed: int = Field(0, alias="已关闭")
    overdue: int = Field(0, alias="逾期工单")
    resolve_rate: float = Field(0.0, alias="解决率(%)")
    by_status: dict[str, int] = Field(default_factory=dict, alias="按状态分布")
    by_priority: dict[str, int] = Field(default_factory=dict, alias="按优先级分布")
    by_type: dict[str, int] = Field(default_factory=dict, alias="按类型分布")
    items: list[dict[str, Any]] = Field(default_factory=list, alias="工单明细", description="工单明细列表")


class CollectedData(BaseModel):
    """从 MySQL 采集的原始数据汇总。"""

    model_config = ConfigDict(populate_by_name=True)

    date_range: str = Field(..., alias="数据时间范围")
    project: ProjectStats = Field(default_factory=ProjectStats, alias="项目数据")
    risk: RiskStats = Field(default_factory=RiskStats, alias="风险数据")
    ticket: TicketStats = Field(default_factory=TicketStats, alias="工单数据")
