"""AI 数据分析平台 · 主 Agent 编排器

整合配置、LLM 客户端和分析引擎，提供统一的对外调用入口。
作为 ``app/ai/agents/AiDataAnalysisPlatform`` 模块的门面（Facade）。
"""

from __future__ import annotations

from typing import AsyncIterator

from .analyzer import DataAnalyzer
from .config import AnalysisConfig
from .llm_client import LLMClient
from .logging_config import get_logger
from .prompts import build_chat_prompt, build_system_prompt
from .schemas import (
    AnalysisResult,
    AnalysisType,
    ChatResponse,
    DataSource,
    HealthResponse,
)

logger = get_logger("Agent")


class DataAnalysisAgent:
    """AI 数据分析 Agent。

    作为整个 AI 数据分析平台的外部门面，对外提供：
    - :meth:`analyze` — 数据分析（同步返回）
    - :meth:`analyze_stream` — 数据分析（流式返回）
    - :meth:`chat` — 快速对话问答
    - :meth:`health_check` — 健康检查

    用法::

        agent = DataAnalysisAgent.from_env()
        result = await agent.analyze(
            data='[{"robot_id": "R001", "fault_code": "E001"}]',
            analysis_type=AnalysisType.FAULT_ANALYSIS,
        )
    """

    def __init__(self, config: AnalysisConfig) -> None:
        self._config = config
        self._llm = LLMClient(config)
        self._analyzer = DataAnalyzer(self._llm)

    # ── 工厂方法 ────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "DataAnalysisAgent":
        """从环境变量创建 Agent 实例。"""
        config = AnalysisConfig.from_env()
        logger.info(
            "DataAnalysisAgent 初始化 provider=%s model=%s",
            config.provider.value,
            config.provider_config.model,
        )
        return cls(config)

    # ── 数据分析 ────────────────────────────────────────────

    async def analyze(
        self,
        data: str,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        question: str | None = None,
        context: str | None = None,
    ) -> AnalysisResult:
        """执行数据分析。

        Args:
            data: 待分析的数据字符串。
            data_source: 数据格式（json / csv / markdown_table / text）。
            analysis_type: 分析类型。
            question: 用户的具体分析问题（可选）。
            context: 补充上下文信息（可选）。

        Returns:
            结构化分析结果。
        """
        return await self._analyzer.analyze(
            data=data,
            data_source=data_source,
            analysis_type=analysis_type,
            question=question,
            context=context,
        )

    async def analyze_stream(
        self,
        data: str,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        question: str | None = None,
        context: str | None = None,
    ) -> AsyncIterator[str]:
        """流式数据分析。

        Args:
            同 :meth:`analyze`。

        Yields:
            模型输出的文本片段。
        """
        async for chunk in self._analyzer.analyze_stream(
            data=data,
            data_source=data_source,
            analysis_type=analysis_type,
            question=question,
            context=context,
        ):
            yield chunk

    # ── 快速对话 ────────────────────────────────────────────

    async def chat(
        self, question: str, context: str | None = None
    ) -> ChatResponse:
        """快速对话问答（无数据分析，纯文本交互）。

        Args:
            question: 用户问题。
            context: 补充上下文（可选）。

        Returns:
            对话响应。
        """
        system_prompt = build_system_prompt(AnalysisType.CUSTOM)
        user_prompt = build_chat_prompt(question, context)

        answer, usage = await self._llm.chat(system_prompt, user_prompt)

        return ChatResponse(
            answer=answer,
            model=self._llm.model_name,
            usage=usage,
        )

    # ── 健康检查 ────────────────────────────────────────────

    def health_check(self) -> HealthResponse:
        """返回平台配置信息（不实际调用 API）。

        base_url 为实际调用的 AI 服务地址（``/api/ai/chat`` 所在服务）。
        """
        return HealthResponse(
            provider=self._config.provider.value,
            model=self._config.provider_config.model,
            base_url=self._config.api_base_url,
        )

    # ── 属性 ────────────────────────────────────────────────

    @property
    def config(self) -> AnalysisConfig:
        """当前配置。"""
        return self._config

    @property
    def model_name(self) -> str:
        """当前模型名称。"""
        return self._llm.model_name
