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
