"""AI 数据分析平台 · 系统提示词模板

为不同分析类型构建结构化的系统提示词，引导大模型产出高质量分析结果。
"""

from __future__ import annotations

from .schemas import AnalysisType, DataSource

# ── 通用系统人设 ────────────────────────────────────────────

_BASE_SYSTEM_PROMPT = """\
你是一个专业的工业机器人数据分析专家，服务于 OpenRobotService 平台。
你擅长分析 AGV/AMR（工业移动机器人）领域的运行数据、故障数据和项目管理数据。

你的分析风格：
- 数据驱动：所有结论都应基于提供的数据，避免臆测。
- 结构清晰：使用标题、列表等格式组织输出，便于阅读。
- 可操作性：给出的建议应当具体、可执行。
- 语言：使用中文回复。
"""

# ── 按分析类型的指令 ────────────────────────────────────────

_TYPE_INSTRUCTIONS: dict[AnalysisType, str] = {
    AnalysisType.GENERAL: """\
## 分析任务
请对提供的数据进行全面分析，包括但不限于：
1. 数据概览（数据量、时间范围、关键指标）
2. 趋势与模式识别
3. 异常值检测
4. 关键发现总结
""",

    AnalysisType.FAULT_ANALYSIS: """\
## 分析任务
请对机器人故障数据进行深入分析：
1. 故障分布统计（按机型、故障码、级别）
2. 故障趋势分析（频率变化、时间聚集性）
3. 高频故障 TOP 5 及可能原因推测
4. 严重故障预警
5. 维护建议
""",

    AnalysisType.TASK_STATS: """\
## 分析任务
请对机器人任务运行统计进行分析：
1. 任务总量与成功率概览
2. 效率指标分析（任务耗时、成功率趋势）
3. 低效机器人识别
4. 优化建议
""",

    AnalysisType.RISK_ASSESSMENT: """\
## 分析任务
请对项目风险数据进行评估：
1. 风险概览（数量、级别分布、状态分布）
2. 高风险项目识别
3. 逾期/临近到期风险
4. 责任人工作负载分析
5. 风险处置建议
""",

    AnalysisType.TREND_PREDICTION: """\
## 分析任务
请基于历史数据进行趋势预测：
1. 历史趋势总结
2. 短期趋势预测（未来 1-2 周）
3. 潜在风险预警
4. 关注重点提示
""",

    AnalysisType.CUSTOM: """\
## 分析任务
请根据用户的分析问题，对数据进行针对性分析，给出专业、详细的回答。
""",
}


def build_system_prompt(analysis_type: AnalysisType) -> str:
    """构建系统提示词。"""
    instruction = _TYPE_INSTRUCTIONS.get(
        analysis_type, _TYPE_INSTRUCTIONS[AnalysisType.GENERAL]
    )
    return f"{_BASE_SYSTEM_PROMPT}\n{instruction}"


def build_user_prompt(
    data: str,
    data_source: DataSource,
    question: str | None = None,
    context: str | None = None,
) -> str:
    """构建用户消息，将数据和问题组合成结构化 prompt。"""

    parts: list[str] = []

    parts.append(f"## 数据格式\n{data_source.value}")

    parts.append(f"## 数据内容\n```\n{data}\n```")

    if context:
        parts.append(f"## 补充上下文\n{context}")

    if question:
        parts.append(f"## 分析问题\n{question}")
    else:
        parts.append("## 分析要求\n请按照上述分析任务对数据进行分析。")

    parts.append(
        "\n## 输出格式要求\n"
        "请使用 Markdown 格式输出，包含以下部分：\n"
        "1. **摘要** — 一句话总结核心发现\n"
        "2. **关键洞察** — 每条洞察用 `###` 标记\n"
        "3. **行动建议** — 用有序列表给出\n"
    )

    return "\n\n".join(parts)


def build_chat_prompt(question: str, context: str | None = None) -> str:
    """构建对话消息的 prompt。"""
    if context:
        return f"## 上下文\n{context}\n\n## 问题\n{question}"
    return question


# ── 指标意图解析 prompt（阶段 2：AnalysisPlanner 专用）────────────────

_PLAN_PARSER_SYSTEM_PROMPT = """\
你是一个数据分析意图解析器，负责把用户的问题解析成结构化的分析计划。

你的输出必须是一个合法的 JSON 对象，不要输出任何其他文字、解释或 Markdown 代码块。
"""

_PLAN_PARSER_JSON_SCHEMA = """\
{
  "metric_keys": ["指标Key数组，只能从上面的指标清单中选，不确定时留空数组"],
  "time_range_type": "时间范围类型，未提及时填 recent_days",
  "time_days": 近N天的天数（仅 recent_days 有效，默认 7）,
  "time_start": "自定义起始日期 YYYY-MM-DD（仅 custom 有效，否则 null）",
  "time_end": "自定义结束日期 YYYY-MM-DD（仅 custom 有效，否则 null）",
  "time_mentioned": true/false（用户是否明确提到了时间范围）,
  "scope_type": "global / single_project / user_projects",
  "project_code": "项目代码（仅 single_project 且能确定时填写，否则 null）",
  "project_name": "用户提到的项目名称（无法映射到代码时保留原文，否则 null）",
  "action": "summary / trend / distribution / compare / top",
  "confidence": 0.0~1.0 的解析置信度
}"""


def build_plan_parser_system_prompt() -> str:
    """构建指标意图解析的系统提示词。"""
    return _PLAN_PARSER_SYSTEM_PROMPT


def build_plan_parser_user_prompt(
    question: str,
    context: str | None = None,
    previous_plan=None,
) -> str:
    """构建指标意图解析的用户消息：指标目录 + 输出格式 + 用户问题。

    Args:
        question: 用户问题。
        context: 补充上下文（可选）。
        previous_plan: 上一轮澄清会话的 AnalysisPlan（可选），供多轮合并。
    """
    from .metric_registry import catalog_for_llm_prompt

    parts: list[str] = []

    parts.append("## 可查询的指标清单\n" + catalog_for_llm_prompt())

    parts.append(
        "## 时间范围类型\n"
        "today / yesterday / recent_days(近N天) / this_week / last_week "
        "/ this_month / last_month / custom(自定义起止)"
    )

    parts.append(
        "## 输出 JSON 格式\n"
        "请只输出一个 JSON 对象，字段如下：\n" + _PLAN_PARSER_JSON_SCHEMA
    )

    if previous_plan is not None:
        parts.append(
            "## 上一轮已解析的分析计划（本轮回答是对其的补充）\n"
            + previous_plan.model_dump_json(indent=2)
        )

    parts.append("## 用户问题\n" + question)

    if context:
        parts.append("## 页面上下文\n" + context)

    parts.append(
        "\n注意：\n"
        "1. 只输出 JSON，不要解释\n"
        "2. metric_keys 只能从指标清单中选择\n"
        "3. 无法判断为数据分析请求时，metric_keys 输出空数组，confidence 输出 0\n"
    )

    return "\n\n".join(parts)
