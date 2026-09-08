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

# LLM 兜底意图判定的系统提示词：要求只输出一个词，便于低成本解析
_INTENT_CLASSIFY_SYSTEM_PROMPT = """\
你是意图分类器。判断用户输入属于哪一类，只输出一个词，不要输出任何其他内容：
- 用户想查询、统计、分析平台数据（项目/工单/任务/风险/指标等）→ 输出 analysis
- 用户只是闲聊、问候、咨询功能用法或其他无关话题 → 输出 chat
"""


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
        # 混合判定：关键词快筛（零成本）→ 无法判定时 LLM 兜底（低温度短回答）
        intent = self._classify_question_intent(question, context)
        if intent == "unknown":
            intent = await self._classify_intent_with_llm(question, context)

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
            # 从问题中解析时间范围（覆盖默认的 today+DAILY）
            resolved_period, resolved_date = self._extract_time_scope(question, period, date)
            # 项目优先级：问题实体提取 > 页面上下文 project_code > 用户关联全部项目
            resolved_project = project_code
            if not resolved_project:
                project_hint = self._extract_project_hint(question)
                if project_hint:
                    resolved_project = ReportGenerator.lookup_project_by_hint(project_hint)

            generator = ReportGenerator(self._llm)
            target_date = _parse_date(resolved_date)
            collected = generator.collect_data(
                period=resolved_period,
                target_date=target_date,
                project_code=resolved_project,
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
                f"统计周期：{resolved_period.value}；"
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
        """基于关键词快筛意图，返回三态：chat / analysis / unknown。

        - 命中礼貌用语 → "chat"（明确闲聊，无需 LLM 判定）
        - 命中分析动作词+主体词组合 → "analysis"（明确分析，无需 LLM 判定）
        - 其余 → "unknown"，交由 :meth:`_classify_intent_with_llm` 用 LLM 兜底判定
        """
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

        return "unknown"

    @staticmethod
    def _extract_project_hint(question: str) -> str | None:
        """从问题中提取项目名线索，供 project 表精确匹配。

        支持的常见表述模式：
        - "XX项目" / "XX 项目" → XX
        - "XX的工单/风险/指标/数据" → XX
        - 引号或书名号内容 「XX」 / "XX" / 《XX》

        返回提取到的线索文本（≥2 字符）或 None。
        """
        # 模式1: "XX项目"
        m = re.search(r'([^，,。.!！？?\s]{2,16})项目', question)
        if m:
            return m.group(1).strip()
        # 模式2: "XX的工单/风险/指标/数据/任务/报障"
        m = re.search(
            r'([^，,。.!！？?\s]{2,16})的(?:工单|风险|指标|数据|任务|项目|报障)',
            question,
        )
        if m:
            return m.group(1).strip()
        # 模式3: 引号/书名号内容 「XX」 / "XX" / 《XX》
        m = re.search(r'[""《]([^""》]{2,20})[""》]', question)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _extract_time_scope(
        question: str,
        default_period: ReportPeriod,
        default_date: str | None,
    ) -> tuple[ReportPeriod, str | None]:
        """从问题中解析时间表述，返回 (period, date_str)。

        仅在问题中有明确时间词时才覆盖默认值；
        否则返回原始 (default_period, default_date)。
        """
        from datetime import date as _dt_date, timedelta as _td

        today = _dt_date.today()
        text = question.lower()

        # 本周 / 这周
        if re.search(r'(?:本周|这周|这礼拜)', text):
            return ReportPeriod.WEEKLY, today.isoformat()
        # 上周
        if re.search(r'上周', text):
            last_monday = today - _td(days=today.weekday() + 7)
            return ReportPeriod.WEEKLY, last_monday.isoformat()
        # 今天
        if re.search(r'今天', text):
            return ReportPeriod.DAILY, today.isoformat()
        # 昨天
        if re.search(r'昨天', text):
            yesterday = today - _td(days=1)
            return ReportPeriod.DAILY, yesterday.isoformat()
        # 前天
        if re.search(r'前天', text):
            two_days_ago = today - _td(days=2)
            return ReportPeriod.DAILY, two_days_ago.isoformat()

        return default_period, default_date

    async def _classify_intent_with_llm(self, question: str, context: str | None = None) -> str:
        """LLM 兜底意图判定：关键词快筛无法判定时调用。

        低温度 + 短回答的轻量调用，避免闲聊场景误判为 analysis 引发无谓查库；
        LLM 异常或回复无法解析时保守回退 "chat"（不阻断主流程）。
        """
        user_prompt = (
            f"## 补充上下文\n{context}\n\n## 用户输入\n{question}"
            if context else f"## 用户输入\n{question}"
        )
        try:
            reply, _ = await self._llm.chat(
                _INTENT_CLASSIFY_SYSTEM_PROMPT,
                user_prompt,
                temperature=0,
                max_tokens=16,
            )
        except Exception:
            logger.warning("LLM 意图兜底判定失败，回退 chat", exc_info=True)
            return "chat"

        text = (reply or "").strip().lower()
        if "analysis" in text:
            return "analysis"
        if "chat" in text:
            return "chat"
        logger.info("LLM 意图判定回复无法解析 %r，回退 chat", text)
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
