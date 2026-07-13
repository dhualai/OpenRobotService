"""AI 数据分析平台

调用线上开源大模型（DeepSeek / Qwen / GLM / SiliconFlow 等）对
结构化和非结构化数据进行智能分析的 Agent 模块。

核心组件：
    - DataAnalysisAgent: 主 Agent，统一对外入口
    - AnalysisConfig: 配置管理
    - LLMClient: 统一 LLM 客户端
    - DataAnalyzer: 数据分析引擎
    - router: FastAPI 路由

快速开始::

    from app.ai.agents.AiDataAnalysisPlatform import DataAnalysisAgent, AnalysisType

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
    AnalysisRequest,
    AnalysisResult,
    AnalysisType,
    ChatResponse,
    DataSource,
    HealthResponse,
    QuickChatRequest,
)

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
    "AnalysisRequest",
    "AnalysisResult",
    "AnalysisType",
    "ChatResponse",
    "DataSource",
    "HealthResponse",
    "QuickChatRequest",
]
