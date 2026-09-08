"""AI 数据分析平台

调用线上开源大模型（DeepSeek / Qwen / GLM / SiliconFlow 等）对
结构化和非结构化数据进行智能分析的 Agent 模块。

核心组件：
    - DataAnalysisAgent: 主 Agent，统一对外入口
    - AnalysisConfig: 配置管理
    - LLMClient: 统一 LLM 客户端
    - DataAnalyzer: 数据分析引擎
    - router: FastAPI 路由
    - metric_registry: 指标注册表（按需分析的指标目录）
    - ReportDataCollector: 数据采集器（支持全量采集 + 按指标采集）
    - AnalysisPlanner: 指标意图解析器（问题 → AnalysisPlan）

快速开始::

    from ai.agents.AiDataAnalysisPlatform import DataAnalysisAgent, AnalysisType

    agent = DataAnalysisAgent.from_env()
    result = await agent.analyze(
        data='[{"robot_id": "R001", "fault_code": "E001"}]',
        analysis_type=AnalysisType.FAULT_ANALYSIS,
    )
    print(result.summary)
"""

from .agent import DataAnalysisAgent
from .analyzer import DataAnalyzer
from .config import AnalysisConfig, LLMProvider, LLMSettings, ProviderConfig
from .llm_client import LLMClient
from .router import router
from .schemas import (
    AnalysisInsight,
    AnalysisPlan,
    AnalysisRequest,
    AnalysisResult,
    AnalysisType,
    ChatResponse,
    DataSource,
    HealthResponse,
    QuickChatRequest,
    ScopeSpec,
    TimeRangeSpec,
)
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
from .report_generator import ReportGenerator, generate_report, generate_report_stream
from .report_generator import ReportDataCollector
from .metric_registry import (
    MetricDef,
    MetricDimension,
    MetricOutputType,
    TimeRangeType,
    METRIC_CATALOG,
    DIMENSION_GROUPS,
    get_metric_def,
    list_metrics_by_dimension,
    catalog_for_llm_prompt,
    resolve_time_range,
)
from .metric_planner import AnalysisPlanner, PlanConversationCache

__all__ = [
    # Agent
    "DataAnalysisAgent",
    # Engine
    "DataAnalyzer",
    # Config
    "AnalysisConfig",
    "LLMProvider",
    "LLMSettings",
    "ProviderConfig",
    # Client
    "LLMClient",
    # Router
    "router",
    # Schemas
    "AnalysisInsight",
    "AnalysisPlan",
    "AnalysisRequest",
    "AnalysisResult",
    "AnalysisType",
    "ChatResponse",
    "DataSource",
    "HealthResponse",
    "QuickChatRequest",
    "ScopeSpec",
    "TimeRangeSpec",
    # Report
    "ReportPeriod",
    "ReportRequest",
    "ReportResult",
    "ReportSection",
    "ProjectStats",
    "RiskStats",
    "TicketStats",
    "CollectedData",
    "ReportGenerator",
    "generate_report",
    "generate_report_stream",
    # Metric Registry
    "MetricDef",
    "MetricDimension",
    "MetricOutputType",
    "TimeRangeType",
    "METRIC_CATALOG",
    "DIMENSION_GROUPS",
    "get_metric_def",
    "list_metrics_by_dimension",
    "catalog_for_llm_prompt",
    "resolve_time_range",
    "ReportDataCollector",
    # Metric Planner
    "AnalysisPlanner",
    "PlanConversationCache",
]
