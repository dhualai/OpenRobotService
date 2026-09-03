"""AI 数据分析平台 · 数据模型

定义分析请求、分析结果及流式输出的 Pydantic schema。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .report_schemas import ReportPeriod


# ── 分析类型 ────────────────────────────────────────────────

class AnalysisType(str, Enum):
    """支持的分析类型。"""

    GENERAL = "general"            # 通用数据分析
    FAULT_ANALYSIS = "fault"       # 机器人故障分析
    TASK_STATS = "task_stats"      # 任务统计分析
    RISK_ASSESSMENT = "risk"       # 风险评估
    TREND_PREDICTION = "trend"     # 趋势预测
    CUSTOM = "custom"              # 自定义分析


class DataSource(str, Enum):
    """数据来源格式。"""

    JSON = "json"
    CSV = "csv"
    MARKDOWN_TABLE = "markdown_table"
    TEXT = "text"


# ── 请求 ────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    """数据分析请求。"""

    data: str = Field(..., description="待分析的数据内容（JSON / CSV / 文本等）")
    data_source: DataSource = Field(
        default=DataSource.JSON, description="数据格式"
    )
    analysis_type: AnalysisType = Field(
        default=AnalysisType.GENERAL, description="分析类型"
    )
    question: str | None = Field(
        default=None, description="用户的具体分析问题（可选）"
    )
    context: str | None = Field(
        default=None, description="补充上下文信息（可选）"
    )
    stream: bool = Field(default=False, description="是否流式输出")


class ChatContextMeta(BaseModel):
    """前端页面上下文元信息，用于补全分析范围。"""

    scene: str | None = Field(
        default=None,
        description="当前页面/场景标识，如 project_detail / dashboard",
    )
    project_code: str | None = Field(
        default=None,
        description="页面上下文中的项目代码；仅在请求未显式传 project_code 时兜底",
    )
    project_name: str | None = Field(
        default=None,
        description="页面上下文中的项目名称，仅用于补充可读上下文",
    )
    user_id: str | None = Field(
        default=None,
        description="页面上下文中的用户ID；仅在请求未显式传 user_id 时兜底",
    )
    period: ReportPeriod | None = Field(
        default=None,
        description="页面上下文中的周期；仅在请求未显式传 period 时兜底",
    )
    date: str | None = Field(
        default=None,
        description="页面上下文中的目标日期 YYYY-MM-DD；仅在请求未显式传 date 时兜底",
    )
    analysis_type: AnalysisType | None = Field(
        default=None,
        description="页面上下文中的推荐分析类型；仅在请求未显式传 analysis_type 时兜底",
    )


class QuickChatRequest(BaseModel):
    """快速对话请求。

    - 仅传 ``question`` / ``context``：自动识别是普通问答还是数据分析。
    - 额外传 ``data``：走带数据上下文的分析问答。
    - 不传 ``data`` 但传 ``project_code`` / ``user_id``：后端自动从 MySQL 采集项目数据后分析。
    - 也可传 ``context_meta`` 作为页面上下文，用于补全分析范围。
    """

    question: str = Field(..., description="用户问题")
    context: str | None = Field(default=None, description="补充上下文")
    data: str | None = Field(
        default=None,
        description="待分析的数据内容；传入后 /chat 会执行数据分析",
    )
    data_source: DataSource = Field(
        default=DataSource.JSON,
        description="data 的数据格式",
    )
    analysis_type: AnalysisType = Field(
        default=AnalysisType.GENERAL,
        description="分析类型；传入 data 或启用自动查库分析时生效",
    )
    project_code: str | None = Field(
        default=None,
        description="项目代码；未传 data 时可用于自动查询该项目数据",
    )
    user_id: str | None = Field(
        default=None,
        description="用户ID或用户名；未传 data 且未传 project_code 时，查询该用户关联项目数据",
    )
    period: ReportPeriod = Field(
        default=ReportPeriod.DAILY,
        description="自动查询数据库时的数据周期：daily / weekly",
    )
    date: str | None = Field(
        default=None,
        description="自动查询数据库时的目标日期 YYYY-MM-DD；默认今天",
    )
    context_meta: ChatContextMeta | None = Field(
        default=None,
        description="前端页面上下文；当显式参数缺失时可用于补充分析范围",
    )


# ── 响应 ────────────────────────────────────────────────────

class AnalysisInsight(BaseModel):
    """单条分析洞察。"""

    category: str = Field(..., description="洞察类别（如：趋势/异常/建议）")
    content: str = Field(..., description="洞察内容")
    severity: str | None = Field(
        default=None, description="严重程度（info/warning/critical）"
    )


class AnalysisResult(BaseModel):
    """完整分析结果。"""

    analysis_type: AnalysisType
    summary: str = Field(..., description="分析摘要")
    insights: list[AnalysisInsight] = Field(
        default_factory=list, description="关键洞察列表"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="行动建议"
    )
    raw_response: str | None = Field(
        default=None, description="大模型原始回复"
    )
    model: str | None = Field(default=None, description="使用的模型名称")
    usage: dict[str, Any] | None = Field(
        default=None, description="token 使用量"
    )


class ChatResponse(BaseModel):
    """对话响应。"""

    answer: str
    mode: str = Field(default="chat", description="响应模式：chat 或 analysis")
    model: str | None = None
    usage: dict[str, Any] | None = None
    analysis: AnalysisResult | None = Field(
        default=None,
        description="带数据分析时返回的结构化分析结果",
    )


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"
    provider: str
    model: str
    base_url: str
