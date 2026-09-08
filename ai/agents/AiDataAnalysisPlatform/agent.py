"""AI 数据分析平台 · 主 Agent 编排器

整合配置、LLM 客户端和分析引擎，提供统一的对外调用入口。
作为 ``app/ai/agents/AiDataAnalysisPlatform`` 模块的门面（Facade）。
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator
from uuid import uuid4

from .analyzer import DataAnalyzer
from .config import AnalysisConfig
from .llm_client import LLMClient
from .logging_config import get_logger
from .metric_planner import AnalysisPlanner, PlanConversationCache
from .metric_registry import TimeRangeType, resolve_time_range
from .prompts import build_chat_prompt, build_system_prompt
from .report_generator import ReportGenerator, ReportDataCollector
from .report_schemas import ReportPeriod
from .schemas import (
    AnalysisPlan,
    AnalysisResult,
    AnalysisType,
    ChatResponse,
    DataSource,
    HealthResponse,
    ScopeSpec,
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
    "新增",
    "新建",
    "数量",
    "多少",
    "几个",
    "怎么样",
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
    r"(新增|新建|逾期|解决率|完成率|等级分布|状态分布|类型分布|优先级分布)",
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
        # 指标意图解析器（阶段 2：问题 → AnalysisPlan）
        self._planner = AnalysisPlanner(self._llm)
        # 澄清会话的 plan 缓存（多轮补充）
        self._plan_cache = PlanConversationCache()

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
        conversation_id: str | None = None,
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
            period: 自动查库时的数据周期（向后兼容保留，指标对话流程由 plan 决定时间范围）。
            date: 自动查库时的目标日期 YYYY-MM-DD（向后兼容保留）。
            conversation_id: 对话会话ID；澄清多轮时关联上一轮解析结果。

        Returns:
            对话响应（chat / analysis / clarify 三种模式）。
        """
        has_data = data is not None and bool(data.strip())
        has_scope = bool(project_code or user_id)
        intent = self._classify_question_intent(question, context)
        # 澄清会话进行中：缓存里有待补充的 plan 时优先进入指标流程
        pending_plan = self._plan_cache.get(conversation_id)

        # 1) 用户提供了数据 → 保持原有分析问答
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

        # 2) 数据分析意图（或显式范围 / 澄清会话中）→ 指标对话流程
        if intent == "analysis" or has_scope or pending_plan is not None:
            # 首次进入无会话ID时自动生成，clarify 响应回传供客户端后续轮次关联
            conversation_id = conversation_id or uuid4().hex
            return await self._chat_metric_flow(
                question=question,
                context=context,
                project_code=project_code,
                user_id=user_id,
                conversation_id=conversation_id,
            )

        # 3) 普通聊天
        return await self._chat_reply(question, context)

    # ── 指标对话主流程（阶段 2）─────────────────────────────

    async def _chat_metric_flow(
        self,
        question: str,
        context: str | None,
        project_code: str | None,
        user_id: str | None,
        conversation_id: str | None,
    ) -> ChatResponse:
        """指标对话主流程：问题 → plan → 澄清/采集 → 分析 → 回答。"""
        previous_plan = self._plan_cache.get(conversation_id)
        plan = await self._planner.parse(question, context, previous_plan)

        # 显式参数覆盖/兜底 plan 的范围
        if project_code:
            plan.scope = ScopeSpec(type="single_project", project_code=project_code)
        elif user_id:
            plan.scope = ScopeSpec(type="user_projects", user_id=user_id)
        elif plan.scope.type == "single_project" and plan.scope.project_name:
            # 用户提到了项目名但未映射出代码 → 查库映射
            code = self._resolve_project_code_by_name(plan.scope.project_name)
            if code:
                plan.scope.project_code = code

        missing = self._planner.missing_fields(plan)
        plan.missing_fields = missing

        # 无法解析出指标且无显式范围 → 回落普通聊天
        if not plan.metric_keys and not (project_code or user_id):
            return await self._chat_reply(question, context)

        if missing:
            # 缓存 plan，等用户下一轮补充
            self._plan_cache.put(conversation_id, plan)
            clarify_text, suggestions = self._planner.build_clarify_questions(
                plan, missing
            )
            return ChatResponse(
                answer=clarify_text,
                mode="clarify",
                model=self._llm.model_name,
                plan=plan,
                suggestions=suggestions,
                conversation_id=conversation_id,
            )

        # plan 完整 → 按指标采集并分析
        try:
            result = await self._analyze_by_plan(plan, question, context)
        except Exception:
            logger.exception("按计划分析失败")
            raise

        self._plan_cache.delete(conversation_id)
        return ChatResponse(
            answer=result.raw_response or result.summary,
            mode="analysis",
            model=result.model,
            usage=result.usage,
            analysis=result,
            plan=plan,
            conversation_id=conversation_id,
        )

    async def _analyze_by_plan(
        self, plan: AnalysisPlan, question: str, context: str | None
    ) -> AnalysisResult:
        """按 AnalysisPlan 采集数据并调用分析引擎。"""
        tr = plan.time_range
        range_type = (
            TimeRangeType(tr.type)
            if tr.type in [t.value for t in TimeRangeType]
            else TimeRangeType.RECENT_DAYS
        )
        try:
            start, end, label = resolve_time_range(
                range_type,
                days=tr.days or 7,
                start_str=tr.start,
                end_str=tr.end,
            )
        except ValueError:
            start, end, label = resolve_time_range(
                TimeRangeType.RECENT_DAYS, days=7
            )

        collector = ReportDataCollector(
            project_ids=self._resolve_plan_project_ids(plan)
        )
        collected = collector.collect_by_plan(
            plan.metric_keys, start, end, label
        )
        collected_data = json.dumps(
            collected, ensure_ascii=False, indent=2, default=str
        )
        collected_context = (
            f"数据来源：OpenRobotService MySQL 实时采集；"
            f"统计周期：{label}；"
            f"统计范围：{plan.scope.type}。"
        )
        merged_context = (
            f"{context}\n\n{collected_context}" if context else collected_context
        )

        return await self._analyzer.analyze(
            data=collected_data,
            data_source=DataSource.JSON,
            analysis_type=AnalysisType.CUSTOM,
            question=question,
            context=merged_context,
        )

    @staticmethod
    def _resolve_plan_project_ids(plan: AnalysisPlan) -> list[str] | None:
        """将 plan 的范围解析为项目过滤列表。

        - single_project → 该项目代码
        - user_projects → 用户关联的全部项目（无项目时用占位符防越权）
        - global → None（不过滤）
        """
        if plan.scope.type == "single_project" and plan.scope.project_code:
            return [plan.scope.project_code]
        if plan.scope.type == "user_projects" and plan.scope.user_id:
            project_ids = ReportGenerator._resolve_project_ids_by_user(
                plan.scope.user_id
            )
            return project_ids or ["__no_project__"]
        return None

    @staticmethod
    def _resolve_project_code_by_name(name: str) -> str | None:
        """按项目名称模糊匹配项目代码。"""
        from ai.core.database import ProjectDelivery, SessionLocal

        db = SessionLocal()
        try:
            row = (
                db.query(ProjectDelivery)
                .filter(ProjectDelivery.name.like(f"%{name}%"))
                .first()
            )
            return row.id if row else None
        except Exception as exc:
            logger.warning("项目名称→代码映射失败: %s", exc)
            return None
        finally:
            db.close()

    async def _chat_reply(
        self, question: str, context: str | None
    ) -> ChatResponse:
        """普通聊天回复。"""
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
