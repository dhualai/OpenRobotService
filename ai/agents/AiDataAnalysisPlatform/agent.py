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
from .chart_builder import build_charts, localize_collected_data, sanitize_field_names
from .config import AnalysisConfig
from .llm_client import LLMClient
from .logging_config import get_logger
from .metric_planner import AnalysisPlanner, PlanConversationCache
from .metric_registry import TimeRangeType, resolve_time_range
from .prompts import build_agentic_system_prompt, build_chat_prompt, build_chat_system_prompt
from .report_generator import ReportGenerator, ReportDataCollector
from .report_schemas import ReportPeriod
from .schemas import (
    AnalysisPlan,
    AnalysisResult,
    AnalysisType,
    ChartSpec,
    ChatResponse,
    DataSource,
    HealthResponse,
    MetricCard,
    ScopeSpec,
)
from .tools import build_agentic_tools, execute_tool

logger = get_logger("Agent")

# Agentic 工具调用最大轮数（防 LLM 反复调工具死循环）
_MAX_AGENTIC_TOOL_ROUNDS = 3

# 统计范围类型 → 中文（喂 LLM 的上下文去技术化用）
_SCOPE_TYPE_CN = {
    "global": "全部项目",
    "single_project": "指定项目",
    "user_projects": "用户关联项目",
}

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
    "搬运",
    "效率",
    "机器人",
    "AGV",
    "agv",
)

_ANALYSIS_STRONG_PATTERNS = (
    r"(最近|本周|上周|本月|近\d+[天周月])",
    r"(风险和工单|工单情况|风险情况|项目情况|任务情况)",
    r"(日报|周报|月报)",
    r"(成功率|完成率|解决率|逾期率|风险分布|工单分布|趋势)",
    r"(新增|新建|逾期|解决率|完成率|等级分布|状态分布|类型分布|优先级分布)",
    r"(搬运效率|机器人组|人工干预率|切手动|搬货|AGV|agv)",
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
        # 混合判定：关键词快筛（零成本）→ 无法判定时 LLM 兜底（低温度短回答）
        intent = self._classify_question_intent(question, context)
        if intent == "unknown":
            intent = await self._classify_intent_with_llm(question, context)
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

        # 3) 普通聊天（带会话记忆）
        # 首问无会话 ID 时自动生成并回传，后续轮次 AI 服务端自动注入历史
        conversation_id = conversation_id or uuid4().hex
        return await self._chat_reply(question, context, conversation_id)

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
        # 会话 ID 保证存在（供 clarify 回传与后续轮次记忆）
        conversation_id = conversation_id or uuid4().hex
        previous_plan = self._plan_cache.get(conversation_id)
        plan = await self._planner.parse(question, context, previous_plan)

        # 显式参数覆盖/兜底 plan 的范围
        self._apply_scope_overrides(plan, question, project_code, user_id)

        missing = self._planner.missing_fields(plan)
        plan.missing_fields = missing

        # 无法解析出指标且无显式范围 → 回落普通聊天
        if not plan.metric_keys and plan.scope.type == "global":
            return await self._chat_reply(question, context, conversation_id)

        if missing:
            # 缓存 plan，等用户下一轮补充
            self._plan_cache.put(conversation_id, plan)
            clarify_text, suggestions = self._planner.build_clarify_questions(
                plan, missing
            )
            if "project_code" in missing:
                # 项目名匹配低置信/多候选：用真实候选项目名渲染消歧按钮
                candidates = self._project_suggestions(question, plan, user_id)
                if candidates:
                    suggestions = candidates + [
                        s for s in suggestions if s not in candidates
                    ]
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
            result, charts, cards = await self._analyze_by_plan(
                plan, question, context, conversation_id
            )
        except Exception:
            logger.exception("按计划分析失败")
            raise

        self._plan_cache.delete(conversation_id)
        return ChatResponse(
            answer=sanitize_field_names(result.raw_response or result.summary),
            mode="analysis",
            model=result.model,
            usage=result.usage,
            analysis=result,
            plan=plan,
            conversation_id=conversation_id,
            charts=charts,
            cards=cards,
        )

    def _apply_scope_overrides(
        self,
        plan: AnalysisPlan,
        question: str,
        project_code: str | None,
        user_id: str | None,
    ) -> None:
        """显式参数覆盖/兜底 plan 的范围（非流式与流式流程共用）。

        优先级：前端显式 project_code > plan 已解析项目 > 问题文本项目名线索
        查库映射 > 登录用户关联项目（user_id）兜底。

        用户明确提到的项目优先于 user_id 兜底，否则澄清轮补充的
        「XX项目」会被 user_projects 覆盖，搬运效率等需明确项目的维度无法收敛。
        """
        if project_code:
            plan.scope = ScopeSpec(type="single_project", project_code=project_code)
            return

        # plan 已解析出的项目信息优先于用户关联项目兜底
        if plan.scope.type == "single_project":
            if not plan.scope.project_code and plan.scope.project_name:
                # 用户提到了项目名但未映射出代码 → 查库映射（优先用户关联项目），
                # 命中后写回 DB 全称（口径回显与回答均展示项目全称）
                code, full_name = self._resolve_project_by_name(
                    plan.scope.project_name, user_id
                )
                if code:
                    plan.scope.project_code = code
                    if full_name:
                        plan.scope.project_name = full_name
            return

        if plan.scope.type == "global":
            # 快路径不解析项目名：问题中出现"XX项目"等实体时查库映射补全范围
            hint = self._extract_project_hint(question)
            if hint:
                code, full_name = self._resolve_project_by_name(hint, user_id)
                if code:
                    plan.scope = ScopeSpec(
                        type="single_project",
                        project_code=code,
                        project_name=full_name or hint,
                    )
                    return

        # 兜底：登录用户关联项目
        if user_id:
            plan.scope = ScopeSpec(type="user_projects", user_id=user_id)

    # ── 流式对话（SSE 事件源，供 router /chat/stream 使用）──────────

    async def chat_stream(
        self,
        question: str,
        context: str | None = None,
        data: str | None = None,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        project_code: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """流式快速对话，逐事件产出 dict（与 :meth:`chat` 同语义）。

        事件协议（router 层序列化为 SSE）：
        - {"type": "meta", "mode": ..., "plan": ..., "charts": ..., "cards": ...,
           "suggestions": ..., "conversation_id": ...}  回答前的结构化元信息
        - {"type": "delta", "content": "..."}            逐块回答文本
        - {"type": "done", "conversation_id": ..., "mode": ...}
        """
        has_data = data is not None and bool(data.strip())
        has_scope = bool(project_code or user_id)
        intent = self._classify_question_intent(question, context)
        if intent == "unknown":
            intent = await self._classify_intent_with_llm(question, context)
        pending_plan = self._plan_cache.get(conversation_id)

        # 1) 用户提供了数据 → 流式分析问答
        if has_data:
            yield {
                "type": "meta", "mode": "analysis", "plan": None,
                "charts": None, "cards": None, "suggestions": None,
                "conversation_id": conversation_id,
            }
            async for chunk in self._analyzer.analyze_stream(
                data=data,
                data_source=data_source,
                analysis_type=analysis_type,
                question=question,
                context=context,
            ):
                yield {"type": "delta", "content": chunk}
            yield {"type": "done", "conversation_id": conversation_id, "mode": "analysis"}
            return

        # 2) 数据分析意图（或显式范围 / 澄清会话中）→ 指标对话流程（流式）
        if intent == "analysis" or has_scope or pending_plan is not None:
            conversation_id = conversation_id or uuid4().hex
            async for event in self._chat_metric_flow_stream(
                question=question,
                context=context,
                project_code=project_code,
                user_id=user_id,
                conversation_id=conversation_id,
            ):
                yield event
            return

        # 3) 普通聊天（流式，带会话记忆）
        # 首问无会话 ID 时自动生成并随 meta 事件回传，后续轮次 AI 服务端自动注入历史
        conversation_id = conversation_id or uuid4().hex
        async for event in self._chat_reply_stream(question, context, conversation_id):
            yield event

    # ── Agentic 自由对话（LLM 主导 + 工具调用，阶段 2）──────────

    async def agentic_chat_stream(
        self,
        question: str,
        context: str | None = None,
        data: str | None = None,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        project_code: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Agentic 流式对话：LLM 主导，自主决定是否调用工具查询指标。

        与 ``chat_stream`` 同 SSE 事件协议（meta → delta* → done）。
        无工具能力（LLM 客户端不支持）或流程异常时自动降级到既有流程。

        Args:
            同 ``chat_stream``（不含 period/date）；data 存在时旁路到原分析流程。
        """
        has_data = data is not None and bool(data.strip())
        # 1) 带 data → 保持原有分析问答（零回归）
        if has_data:
            async for event in self.chat_stream(
                question=question, context=context, data=data,
                data_source=data_source, analysis_type=analysis_type,
                project_code=project_code, user_id=user_id,
                conversation_id=conversation_id,
            ):
                yield event
            return

        conversation_id = conversation_id or uuid4().hex

        # LLM 客户端不支持工具调用（旧服务/测试 Mock）→ 降级既有流程
        if not hasattr(self._llm, "chat_with_tools"):
            logger.info("LLM 客户端不支持工具调用，agentic 降级 chat_stream")
            async for event in self.chat_stream(
                question=question, context=context,
                project_code=project_code, user_id=user_id,
                conversation_id=conversation_id,
            ):
                yield event
            return

        try:
            async for event in self._agentic_flow(
                question=question, context=context,
                project_code=project_code, user_id=user_id,
                conversation_id=conversation_id,
            ):
                yield event
        except Exception:
            logger.exception("agentic 流程失败，降级普通聊天")
            async for event in self._chat_reply_stream(
                question, context, conversation_id
            ):
                yield event

    async def _agentic_flow(
        self,
        question: str,
        context: str | None,
        project_code: str | None,
        user_id: str | None,
        conversation_id: str,
    ) -> AsyncIterator[dict]:
        """ReAct 循环：LLM 决策（是否调工具）↔ 工具执行 → 自由组织回答。

        messages 由 agent 全程维护（system + 历史 + 工具往返），经
        ``chat_with_tools(messages=...)`` 直连 AI 服务；最终问答对显式
        落库会话记忆（中间工具轮不落库，防历史污染）。
        """
        # 1. 会话历史（多轮记忆）：AI 服务端 Redis → messages 历史段
        history_msgs: list[dict] = []
        if hasattr(self._llm, "fetch_history"):
            history_msgs = await self._llm.fetch_history(conversation_id)

        # 2. 初始 messages：system + 历史 + 用户问题
        messages: list[dict] = [
            {"role": "system", "content": build_agentic_system_prompt()}
        ]
        messages.extend(history_msgs)
        user_text = question
        if context:
            user_text = f"## 页面上下文\n{context}\n\n## 用户问题\n{question}"
        if project_code:
            user_text += f"\n（当前页面项目代码：{project_code}，查询该项目数据时优先使用）"
        messages.append({"role": "user", "content": user_text})

        tools = build_agentic_tools()
        charts_out: list = []
        cards_out: list = []
        used_tool = False
        final_answer = ""

        # 3. ReAct 循环（最多 _MAX_AGENTIC_TOOL_ROUNDS 轮工具调用）
        for _round in range(_MAX_AGENTIC_TOOL_ROUNDS + 1):
            # system 已置于 messages[0]；此处 system_prompt 传空（messages 模式下忽略）
            resp = await self._llm.chat_with_tools(
                "", user_text, tools,
                messages=messages,
                session_id=conversation_id,
                temperature=0.3,
                save_memory=False,  # 中间轮一律不落库，最终问答对由第 4 步显式保存
            )
            content = (resp.get("content") or "").strip()
            tool_calls = resp.get("tool_calls") or []
            if not tool_calls:
                final_answer = content
                break

            # 有工具调用：归一化 call id，回填 assistant(tool_calls) 与 tool 结果
            used_tool = True
            call_ids = [
                tc.get("id") or f"call_{i}" for i, tc in enumerate(tool_calls)
            ]
            assistant_msg: dict = {"role": "assistant", "content": content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": call_ids[i],
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(
                            tc.get("arguments") or {}, ensure_ascii=False
                        ),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ]
            messages.append(assistant_msg)
            for i, tc in enumerate(tool_calls):
                try:
                    result = await execute_tool(
                        tc.get("name", ""), tc.get("arguments") or {},
                        self, user_id,
                    )
                except Exception:
                    logger.exception(
                        "agentic 工具执行失败 name=%s", tc.get("name")
                    )
                    result = {"text": "工具执行失败，请换一种问法或稍后重试。"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_ids[i],
                    "content": result.get("text") or "",
                })
                charts_out.extend(result.get("charts") or [])
                cards_out.extend(result.get("cards") or [])
        else:
            # 循环耗尽仍无最终答案（连续多轮全是工具调用）
            final_answer = final_answer or (
                "抱歉，这个问题有些复杂，请换个方式描述，"
                "或明确要查询的指标与时间范围。"
            )

        # 4. 最终问答对显式落库会话记忆（一次交互只记一轮，中间工具轮不记）
        if final_answer.strip() and hasattr(self._llm, "add_turn"):
            await self._llm.add_turn(conversation_id, "user", question)
            await self._llm.add_turn(conversation_id, "assistant", final_answer)

        # 5. SSE 下发：meta → delta* → done
        mode = "analysis" if used_tool else "chat"
        yield {
            "type": "meta", "mode": mode, "plan": None,
            "charts": charts_out or None,
            "cards": cards_out or None,
            "suggestions": None,
            "conversation_id": conversation_id,
        }
        # 最终答案按块下发（整段答案已生成，逐块输出保持前端流式观感）
        for i in range(0, len(final_answer), 24):
            yield {"type": "delta", "content": final_answer[i:i + 24]}
        yield {"type": "done", "conversation_id": conversation_id, "mode": mode}

    async def _chat_metric_flow_stream(
        self,
        question: str,
        context: str | None,
        project_code: str | None,
        user_id: str | None,
        conversation_id: str | None,
    ) -> AsyncIterator[dict]:
        """指标对话主流程（流式）：问题 → plan → 采集 → 流式分析。

        与 ``_chat_metric_flow`` 同逻辑，区别是图表/卡片随 meta 事件先行下发，
        回答文本经 delta 事件逐块输出。
        """
        # 会话 ID 保证存在（供 clarify 回传与后续轮次记忆）
        conversation_id = conversation_id or uuid4().hex
        previous_plan = self._plan_cache.get(conversation_id)
        plan = await self._planner.parse(question, context, previous_plan)

        self._apply_scope_overrides(plan, question, project_code, user_id)

        missing = self._planner.missing_fields(plan)
        plan.missing_fields = missing

        # 无法解析出指标且无显式范围 → 回落普通聊天（流式）
        if not plan.metric_keys and plan.scope.type == "global":
            async for event in self._chat_reply_stream(question, context, conversation_id):
                yield event
            return

        if missing:
            # 缓存 plan，等用户下一轮补充
            self._plan_cache.put(conversation_id, plan)
            clarify_text, suggestions = self._planner.build_clarify_questions(
                plan, missing
            )
            if "project_code" in missing:
                # 项目名匹配低置信/多候选：用真实候选项目名渲染消歧按钮
                candidates = self._project_suggestions(question, plan, user_id)
                if candidates:
                    suggestions = candidates + [
                        s for s in suggestions if s not in candidates
                    ]
            yield {
                "type": "meta", "mode": "clarify", "plan": plan.model_dump(),
                "charts": None, "cards": None, "suggestions": suggestions,
                "conversation_id": conversation_id,
            }
            yield {"type": "delta", "content": clarify_text}
            yield {"type": "done", "conversation_id": conversation_id, "mode": "clarify"}
            return

        # plan 完整 → 采集数据并生成图表（LLM 前的确定性部分）
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
        charts, cards = build_charts(plan.metric_keys, collected)
        collected_data = json.dumps(
            localize_collected_data(collected), ensure_ascii=False, indent=2, default=str
        )
        scope_cn = _SCOPE_TYPE_CN.get(plan.scope.type, plan.scope.type)
        if plan.scope.type == "single_project" and plan.scope.project_code:
            scope_cn = (
                f"指定项目「{plan.scope.project_name}」（项目编号 {plan.scope.project_code}）"
                if plan.scope.project_name
                else f"指定项目（项目编号 {plan.scope.project_code}）"
            )
        collected_context = (
            f"数据来源：平台实时统计；"
            f"统计周期：{label}；"
            f"统计范围：{scope_cn}。"
            f"回答中提及项目时请使用项目完整名称。"
            f"回答仅基于本次采集的数据，历史对话仅供参考。"
        )
        merged_context = (
            f"{context}\n\n{collected_context}" if context else collected_context
        )

        self._plan_cache.delete(conversation_id)

        # 图表/卡片/口径先行下发，回答文本流式追加
        yield {
            "type": "meta", "mode": "analysis", "plan": plan.model_dump(),
            "charts": [c.model_dump() for c in charts],
            "cards": [c.model_dump() for c in cards],
            "suggestions": None,
            "conversation_id": conversation_id,
        }
        async for chunk in self._analyzer.analyze_stream(
            data=collected_data,
            data_source=DataSource.JSON,
            analysis_type=AnalysisType.CUSTOM,
            question=question,
            context=merged_context,
            session_id=conversation_id,
        ):
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "conversation_id": conversation_id, "mode": "analysis"}

    async def _chat_reply_stream(
        self, question: str, context: str | None, conversation_id: str
    ) -> AsyncIterator[dict]:
        """普通聊天回复（流式，带会话记忆）。

        使用自由对话人设（与数据分析人设分离），并以 conversation_id
        作为 AI 服务端 session_id，复用历史对话实现多轮记忆。
        """
        system_prompt = build_chat_system_prompt()
        user_prompt = build_chat_prompt(question, context)

        yield {
            "type": "meta", "mode": "chat", "plan": None,
            "charts": None, "cards": None, "suggestions": None,
            "conversation_id": conversation_id,
        }
        async for chunk in self._llm.chat_stream(
            system_prompt, user_prompt, session_id=conversation_id
        ):
            yield {"type": "delta", "content": chunk}
        yield {"type": "done", "conversation_id": conversation_id, "mode": "chat"}

    async def _analyze_by_plan(
        self,
        plan: AnalysisPlan,
        question: str,
        context: str | None,
        conversation_id: str | None,
    ) -> tuple[AnalysisResult, list[ChartSpec], list[MetricCard]]:
        """按 AnalysisPlan 采集数据并调用分析引擎。

        Args:
            conversation_id: 会话 ID；透传给分析引擎作为 session_id，
                注入会话历史（多轮追问可承上文）；None 时无状态分析。

        Returns:
            (分析结果, 图表列表, 指标卡片列表)；图表/卡片由采集结果直接生成。
        """
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
        # 图表/卡片由采集结果直接生成；喂 LLM 的数据转为中文 key（根除字段名泄漏）
        charts, cards = build_charts(plan.metric_keys, collected)
        collected_data = json.dumps(
            localize_collected_data(collected), ensure_ascii=False, indent=2, default=str
        )
        scope_cn = _SCOPE_TYPE_CN.get(plan.scope.type, plan.scope.type)
        if plan.scope.type == "single_project" and plan.scope.project_code:
            scope_cn = (
                f"指定项目「{plan.scope.project_name}」（项目编号 {plan.scope.project_code}）"
                if plan.scope.project_name
                else f"指定项目（项目编号 {plan.scope.project_code}）"
            )
        collected_context = (
            f"数据来源：平台实时统计；"
            f"统计周期：{label}；"
            f"统计范围：{scope_cn}。"
            f"回答中提及项目时请使用项目完整名称。"
            f"回答仅基于本次采集的数据，历史对话仅供参考。"
        )
        merged_context = (
            f"{context}\n\n{collected_context}" if context else collected_context
        )

        result = await self._analyzer.analyze(
            data=collected_data,
            data_source=DataSource.JSON,
            analysis_type=AnalysisType.CUSTOM,
            question=question,
            context=merged_context,
            session_id=conversation_id,
        )
        return result, charts, cards

    @staticmethod
    def _sanitize_field_names(text: str) -> str:
        """兜底替换 LLM 回答中残留的英文字段名（复用 chart_builder 映射表）。"""
        return sanitize_field_names(text)

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

    def _resolve_project_by_name(
        self, name: str, user_id: str | None = None
    ) -> tuple[str | None, str | None]:
        """按项目名称模糊匹配项目，返回 (项目代码, DB 全称)。

        有 user_id 时优先在用户关联项目（user_project_roles）范围内匹配，
        避免命中其他用户的同名/相似名项目；用户范围内无命中或未传
        user_id 时回退全局匹配。多候选并列/低置信时不猜（返回
        (None, None)），由 clarify 候选按钮消歧。
        """
        from .project_matcher import resolve_project

        scope_ids: list[str] | None = None
        if user_id:
            try:
                scope_ids = ReportGenerator._resolve_project_ids_by_user(user_id) or None
            except Exception as exc:
                logger.warning("用户关联项目查询失败: %s", exc)
        if scope_ids:
            code, matches = resolve_project(name, scope_ids)
            if code and matches:
                return code, matches[0].name
        code, matches = resolve_project(name)
        if code and matches:
            return code, matches[0].name
        return None, None

    def _project_suggestions(
        self, question: str, plan: AnalysisPlan, user_id: str | None
    ) -> list[str]:
        """project_code 缺失时的候选项目名列表（clarify 消歧按钮用）。

        从本轮问题文本提取项目线索（或取 plan 已解析的项目名），
        在用户关联项目范围内召回候选；无候选时回退全局召回。
        """
        hint = self._extract_project_hint(question)
        if not hint and plan.scope.type == "single_project":
            hint = plan.scope.project_name
        if not hint:
            return []
        from .project_matcher import match_projects

        scope_ids: list[str] | None = None
        if user_id:
            try:
                scope_ids = ReportGenerator._resolve_project_ids_by_user(user_id) or None
            except Exception:
                scope_ids = None
        cands = match_projects(hint, scope_ids)
        if not cands:
            cands = match_projects(hint)
        if not cands:
            return []
        # 并列候选一并返回（分数差距 <0.05 视为并列），避免目标项目被截断；
        # 按钮上限 4 个，超出则取前 4
        top_score = cands[0].score
        picks = [c for c in cands if c.score >= top_score - 0.05][:4]
        return [c.name for c in picks]

    async def _chat_reply(
        self, question: str, context: str | None, conversation_id: str
    ) -> ChatResponse:
        """普通聊天回复（带会话记忆）。

        使用自由对话人设（与数据分析人设分离），并以 conversation_id
        作为 AI 服务端 session_id，复用历史对话实现多轮记忆。
        """
        system_prompt = build_chat_system_prompt()
        user_prompt = build_chat_prompt(question, context)

        answer, usage = await self._llm.chat(
            system_prompt, user_prompt, session_id=conversation_id
        )

        return ChatResponse(
            answer=answer,
            mode="chat",
            model=self._llm.model_name,
            usage=usage,
            conversation_id=conversation_id,
        )

    @staticmethod
    def _classify_question_intent(question: str, context: str | None = None) -> str:
        """基于问题文本快速识别意图（混合判定第一步：关键词快筛）。

        返回三态：
        - "chat"：明确的礼貌用语/闲聊
        - "analysis"：明确的指标分析意图
        - "unknown"：关键词无法判定，交由 :meth:`_classify_intent_with_llm` LLM 兜底
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
