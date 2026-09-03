"""AI 数据分析平台 · 主 Agent 编排器

整合配置、LLM 客户端和分析引擎，提供统一的对外调用入口。
作为 ``app/ai/agents/AiDataAnalysisPlatform`` 模块的门面（Facade）。
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator

from .analyzer import DataAnalyzer
from .config import AnalysisConfig
from .llm_client import LLMClient
from .logging_config import get_logger
from .prompts import build_chat_prompt, build_system_prompt
from .report_generator import ReportGenerator, _parse_date
from .report_schemas import ReportPeriod
from .schemas import (
    AnalysisResult,
    AnalysisType,
    ChatResponse,
    DataSource,
    HealthResponse,
)

logger = get_logger("Agent")

_COURTESY_PATTERNS = (
    r"^(你好|您好|hi|hello|在吗|在不在)[!！。,. ]*$",
    r"^(谢谢|多谢|辛苦了|辛苦啦|收到|好的|好嘞)[!！。,. ]*$",
)

_ANALYSIS_ACTION_KEYWORDS = (
    "分析",
    "统计",
    "评估",
    "预测",
    "概览",
    "总结",
    "报表",
    "趋势",
    "分布",
    "占比",
    "同比",
    "环比",
    "top",
)

_ANALYSIS_SUBJECT_KEYWORDS = (
    "项目",
    "工单",
    "任务",
    "风险",
    "数据",
    "指标",
    "日报",
    "周报",
    "月报",
    "成功率",
    "逾期",
    "故障",
)

_ANALYSIS_STRONG_PATTERNS = (
    r"(最近|本周|上周|本月|近\d+[天周月])",
    r"(风险和工单|工单情况|风险情况|项目情况|任务情况)",
    r"(日报|周报|月报)",
    r"(成功率|完成率|解决率|逾期率|风险分布|工单分布|趋势)",
)


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
        self,
        question: str,
        context: str | None = None,
        data: str | None = None,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        project_code: str | None = None,
        user_id: str | None = None,
        period: ReportPeriod = ReportPeriod.DAILY,
        date: str | None = None,
    ) -> ChatResponse:
        """快速对话问答。

        Args:
            question: 用户问题。
            context: 补充上下文（可选）。
            data: 待分析的数据；传入后复用分析引擎进行分析问答。
            data_source: 数据格式。
            analysis_type: 分析类型。
            project_code: 项目代码；未传 data 时可自动按项目查库。
            user_id: 用户ID或用户名；未传 data 且未传 project_code 时可按用户查库。
            period: 自动查库时的数据周期。
            date: 自动查库时的目标日期 YYYY-MM-DD。

        Returns:
            对话响应。
        """
        has_data = data is not None and bool(data.strip())
        has_scope = bool(project_code or user_id)
        intent = self._classify_question_intent(question, context)

        if has_data:
            result = await self._analyzer.analyze(
                data=data,
                data_source=data_source,
                analysis_type=analysis_type,
                question=question,
                context=context,
            )
            return ChatResponse(
                answer=result.raw_response or result.summary,
                mode="analysis",
                model=result.model,
                usage=result.usage,
                analysis=result,
            )

        if has_scope and intent == "analysis":
            generator = ReportGenerator(self._llm)
            target_date = _parse_date(date)
            collected = generator.collect_data(
                period=period,
                target_date=target_date,
                project_code=project_code,
                user_id=user_id,
            )
            collected_data = json.dumps(
                collected.model_dump(by_alias=True),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            collected_context = (
                f"数据来源：OpenRobotService MySQL 实时采集；"
                f"统计周期：{period.value}；"
                f"统计范围：{collected.date_range}。"
            )
            merged_context = (
                f"{context}\n\n{collected_context}" if context else collected_context
            )
            result = await self._analyzer.analyze(
                data=collected_data,
                data_source=DataSource.JSON,
                analysis_type=analysis_type,
                question=question,
                context=merged_context,
            )
            return ChatResponse(
                answer=result.raw_response or result.summary,
                mode="analysis",
                model=result.model,
                usage=result.usage,
                analysis=result,
            )

        if intent == "analysis":
            raise ValueError(
                "识别到数据分析意图，但缺少 data 或 project_code/user_id。"
                "请补充分析数据，或传 project_code/user_id 让后端自动查库。"
            )

        system_prompt = build_system_prompt(AnalysisType.CUSTOM)
        user_prompt = build_chat_prompt(question, context)

        answer, usage = await self._llm.chat(system_prompt, user_prompt)

        return ChatResponse(
            answer=answer,
            mode="chat",
            model=self._llm.model_name,
            usage=usage,
        )

    @staticmethod
    def _classify_question_intent(question: str, context: str | None = None) -> str:
        """基于问题文本自动识别是普通聊天还是数据分析。"""
        text = "\n".join(part.strip() for part in [question, context or ""] if part).lower()
        if not text:
            return "chat"

        if any(re.fullmatch(pattern, text) for pattern in _COURTESY_PATTERNS):
            return "chat"

        strong_hit = any(re.search(pattern, text) for pattern in _ANALYSIS_STRONG_PATTERNS)
        action_hit = any(keyword in text for keyword in _ANALYSIS_ACTION_KEYWORDS)
        subject_hit = any(keyword in text for keyword in _ANALYSIS_SUBJECT_KEYWORDS)

        if strong_hit and (action_hit or subject_hit):
            return "analysis"
        if action_hit and subject_hit:
            return "analysis"

        return "chat"

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
