"""AI 数据分析平台 · 数据模型

定义分析请求、分析结果及流式输出的 Pydantic schema。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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


class QuickChatRequest(BaseModel):
    """快速对话请求（无数据，仅文本问答）。"""

    question: str = Field(..., description="用户问题")
    context: str | None = Field(default=None, description="补充上下文")


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
    model: str | None = None
    usage: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"
    provider: str
    model: str
    base_url: str
