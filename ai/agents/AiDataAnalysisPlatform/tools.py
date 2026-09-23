"""AI 数据分析平台 · Agentic 工具集

为「LLM 主导的自由对话」（agentic_chat）提供可调用工具：
- ``query_metrics``  查询平台统计指标（复用 metric_registry + ReportDataCollector + chart_builder）
- ``list_projects``  按名称线索召回项目候选（复用 project_matcher）

工具定义遵循 OpenAI tools 协议，由 AI 服务侧 ``complete_with_tools`` 消费；
执行器在本模块内实现，由 agent 的 ReAct 循环调用。
"""
from __future__ import annotations

import json

from .chart_builder import build_charts, localize_collected_data
from .logging_config import get_logger
from .metric_registry import (
    TimeRangeType,
    get_metric_def,
    resolve_time_range,
)

logger = get_logger("Tools")

# ── 工具定义（OpenAI tools 协议）────────────────────────────

QUERY_METRICS_TOOL = {
    "type": "function",
    "function": {
        "name": "query_metrics",
        "description": (
            "查询 OpenRobotService 平台统计指标（工单/风险/项目/搬运效率/项目信息等）。"
            "当用户询问平台数据、统计、趋势、分布、对比时调用。"
            "metric_keys 必须从指标清单中选择。"
            "时间口径：project 维度指标按 settlement_period 业绩核算期（月份）过滤，"
            "用户未提及时间时不要传任何时间参数（查全部项目）；"
            "风险维度为规则推算口径（项目信息 AGV 数量/车型、项目类型、人工风险点 + 关联工单未关闭数/近30天新增综合评分），"
            "不依赖时间范围；工单/搬运效率/项目信息等维度未提及时间时用 recent_days 近7天。"
            "用户只提月份（如「9月份」）时默认当前年份（月份在未来则回退上一年），"
            "用 custom 时间范围查询。"
            "项目未提及时默认只查用户关联的项目（scope_type=user_projects），不要为确认项目而停下；"
            "scope_type 不传或传 global 时系统同样只统计用户关联的项目；"
            "项目名无法唯一确定时才先调 list_projects 消歧。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要查询的指标 key 列表（如 ['ticket.resolve_rate']），只能从指标清单中选择",
                },
                "time_range_type": {
                    "type": "string",
                    "enum": [t.value for t in TimeRangeType],
                    "description": "时间范围类型，默认 recent_days",
                },
                "time_days": {
                    "type": "integer",
                    "description": "近 N 天（仅 recent_days 有效，默认 7）",
                },
                "time_start": {
                    "type": "string",
                    "description": "自定义起始日期 YYYY-MM-DD（仅 custom 有效）",
                },
                "time_end": {
                    "type": "string",
                    "description": "自定义结束日期 YYYY-MM-DD（仅 custom 有效）",
                },
                "scope_type": {
                    "type": "string",
                    "enum": ["global", "single_project", "user_projects"],
                    "description": "统计范围：single_project 指定项目 / user_projects 用户关联项目（默认）/ global 全部项目",
                },
                "project_code": {
                    "type": "string",
                    "description": "项目代码（single_project 且已确定时填写）",
                },
                "project_name": {
                    "type": "string",
                    "description": "项目名称线索（用户提到的项目名，由系统映射为代码）",
                },
            },
            "required": ["metric_keys"],
        },
    },
}

LIST_PROJECTS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_projects",
        "description": (
            "按名称线索查找平台项目候选列表。当用户提到的项目名无法确定唯一项目时，"
            "先调用本工具消歧，再以候选中的项目编号调用 query_metrics。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_hint": {
                    "type": "string",
                    "description": "用户话里的项目名线索，如「罗勇项目」",
                },
            },
            "required": ["name_hint"],
        },
    },
}


def build_agentic_tools() -> list:
    """返回 agentic 对话可用的工具定义列表。"""
    return [QUERY_METRICS_TOOL, LIST_PROJECTS_TOOL]


# ── 工具执行器 ─────────────────────────────────────────────

async def execute_tool(name: str, arguments: dict, agent, user_id: str | None = None) -> dict:
    """执行单个工具调用。

    Args:
        name: 工具名（query_metrics / list_projects）。
        arguments: LLM 给出的参数（dict，已解析）。
        agent: DataAnalysisAgent 实例（提供项目解析等上下文能力）。
        user_id: 登录用户 ID（user_projects 范围解析用）。

    Returns:
        {"text": 回填给 LLM 的结果文本, "charts": [...] | None,
         "cards": [...] | None, "candidates": [...] | None}
    """
    if name == "query_metrics":
        return await _exec_query_metrics(arguments, agent, user_id)
    if name == "list_projects":
        return _exec_list_projects(arguments, agent, user_id)
    logger.warning("未知工具调用 %r，返回提示", name)
    return {"text": f"未知工具 {name}，请改用可用工具。"}


async def _exec_query_metrics(args: dict, agent, user_id: str | None) -> dict:
    """执行指标查询：白名单校验 → 时间范围 → 范围解析 → 采集 → 图表/卡片。"""
    from .report_generator import ReportDataCollector

    # 1. 指标白名单校验（防幻觉 key）
    metric_keys = [k for k in (args.get("metric_keys") or []) if get_metric_def(k)]
    if not metric_keys:
        return {"text": "未指定有效指标，请先从指标清单中选择要查询的指标。"}

    # 2. 时间范围；时间是否显式提及——project 维度指标仅在显式时间下
    #    按 settlement_period（业绩核算期）月份过滤，未提及时查全部。
    explicit_time = bool(
        args.get("time_range_type") or args.get("time_start")
        or args.get("time_end") or args.get("time_days")
    )
    tr_type = args.get("time_range_type") or "recent_days"
    if tr_type not in [t.value for t in TimeRangeType]:
        tr_type = "recent_days"
    try:
        start, end, label = resolve_time_range(
            TimeRangeType(tr_type),
            days=int(args.get("time_days") or 7),
            start_str=args.get("time_start"),
            end_str=args.get("time_end"),
        )
    except (ValueError, TypeError):
        start, end, label = resolve_time_range(TimeRangeType.RECENT_DAYS, days=7)

    # 3. 范围解析（借用 agent 的项目解析能力，保证与既有流程同口径）
    project_ids, scope_label = await _resolve_scope(args, agent, user_id)

    # 4. 采集 + 图表/卡片（LLM 前确定性产出，同 _analyze_by_plan 口径）
    try:
        collector = ReportDataCollector(project_ids=project_ids)
        collected = collector.collect_by_plan(
            metric_keys, start, end, label, explicit_time=explicit_time
        )
    except Exception:
        logger.exception("agentic 指标采集失败 keys=%s", metric_keys)
        return {"text": "指标数据采集失败，请稍后重试或调整查询条件。"}

    charts, cards = build_charts(metric_keys, collected)
    data_text = json.dumps(
        localize_collected_data(collected), ensure_ascii=False, indent=2, default=str
    )

    result_text = (
        f"统计周期：{label}；统计范围：{scope_label}。\n"
        f"系统已为这些指标生成图表/卡片展示，回答请图+文字结合："
        f"文字部分解读趋势走向、分布结构、对比差异与关键洞察，"
        f"不要大段复述与图表重复的数字，不要复述 JSON。\n"
        f"数据（JSON）：\n{data_text}"
    )
    # 前端展示元信息：项目名大标题（单项目优先取数据库项目全名，不依赖
    # LLM 传参，避免标题退化成「指定项目（编号）」）与数据日期（具体年月日），
    # 随 meta 事件下发给前端渲染
    scope_title = scope_label
    if project_ids:
        db_name = (
            _project_name_by_code(project_ids[0])
            if len(project_ids) == 1
            else None
        )
        scope_title = db_name or args.get("project_name") or scope_label
    return {
        "text": result_text,
        "charts": [c.model_dump() for c in charts],
        "cards": [c.model_dump() for c in cards],
        "scope_title": scope_title,
        "date_range": label,
    }


async def _resolve_scope(args: dict, agent, user_id: str | None) -> tuple[list[str] | None, str]:
    """将工具参数解析为 (项目过滤列表, 范围中文描述)。

    与 agent._apply_scope_overrides 口径一致：优先显式项目 → 项目名查库映射
    → 用户关联项目 → 全局。
    """
    from .report_generator import ReportGenerator

    scope_type = args.get("scope_type") or "global"
    project_code = args.get("project_code")
    project_name = args.get("project_name")

    if scope_type == "single_project":
        if not project_code and project_name:
            try:
                code, full_name = agent._resolve_project_by_name(project_name, user_id)
                if code:
                    project_code = code
                    project_name = full_name or project_name
            except Exception as exc:
                logger.warning("agentic 项目名映射失败: %s", exc)
        if project_code:
            label = (
                f"指定项目「{project_name}」（{project_code}）"
                if project_name
                else f"指定项目（{project_code}）"
            )
            return [project_code], label
        # 项目未确定：退回用户关联项目范围（有 user_id 时），否则全局
        if user_id:
            try:
                ids = ReportGenerator._resolve_project_ids_by_user(user_id)
            except Exception:
                ids = None
            if ids:
                return ids, f"用户关联项目（{len(ids)} 个）"
        return None, "全部项目"

    if scope_type == "user_projects" and user_id:
        try:
            ids = ReportGenerator._resolve_project_ids_by_user(user_id)
        except Exception:
            ids = None
        if ids:
            return ids, f"用户关联项目（{len(ids)} 个）"
        return ["__no_project__"], "用户关联项目（无关联项目）"

    # 未显式指定（global/未传）：有登录用户时默认只查用户关联项目，
    # 与既有流程 _apply_scope_overrides 口径一致；无 user_id 才查全部。
    if user_id:
        try:
            ids = ReportGenerator._resolve_project_ids_by_user(user_id)
        except Exception:
            ids = None
        if ids:
            return ids, f"用户关联项目（{len(ids)} 个）"
        return ["__no_project__"], "用户关联项目（无关联项目）"

    return None, "全部项目"


def _project_name_by_code(code: str) -> str | None:
    """按项目代码从数据库查项目全名（复用项目名 TTL 缓存）；失败返回 None。"""
    try:
        from .project_matcher import get_project_name

        return get_project_name(code)
    except Exception:
        logger.warning("agentic 项目名查询失败 code=%s", code)
        return None


def _exec_list_projects(args: dict, agent, user_id: str | None) -> dict:
    """项目候选召回：优先在用户关联项目范围内匹配，无命中回退全局。"""
    from .project_matcher import match_projects
    from .report_generator import ReportGenerator

    hint = (args.get("name_hint") or "").strip()
    if len(hint) < 2:
        return {"text": "项目名线索过短，请提供更具体的项目名称。"}

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
        return {
            "text": f"未找到与「{hint}」匹配的项目，请确认项目名称，或改为全局查询。"
        }

    lines = [f"与「{hint}」匹配的项目候选："]
    for c in cands:
        lines.append(f"- {c.name}（项目编号 {c.code}，匹配度 {c.score:.2f}）")
    return {
        "text": "\n".join(lines),
        "candidates": [c.name for c in cands],
    }
