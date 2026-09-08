"""AI 数据分析平台 · 指标注册表

定义所有可查询的分析指标，是「指标解析器」和「按需采集器」之间的唯一契约。

每个指标包含：
- key: 唯一标识（如 "ticket.resolve_rate"）
- dimension: 所属维度（ticket / project / risk）
- label: 中文名称
- desc: 简短说明，喂给 LLM 做指标选择
- requires_time_range: 是否需要时间范围
- output_type: 输出形态（scalar / distribution / trend / list）
- collect_fn: 采集函数名（在 ReportDataCollector 上的方法名）

用法::

    from .metric_registry import METRIC_CATALOG, get_metric_def, catalog_for_llm_prompt

    # 按 key 查定义
    metric = get_metric_def("ticket.resolve_rate")

    # 生成 LLM prompt 中的指标清单
    prompt_text = catalog_for_llm_prompt()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar


# ── 时间范围类型 ──────────────────────────────────────────────


class TimeRangeType(str, Enum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    RECENT_DAYS = "recent_days"       # 最近 N 天
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    CUSTOM = "custom"                 # 显式指定 start / end


# ── 指标维度 ──────────────────────────────────────────────────


class MetricDimension(str, Enum):
    TICKET = "ticket"
    PROJECT = "project"
    RISK = "risk"


# ── 输出形态 ──────────────────────────────────────────────────


class MetricOutputType(str, Enum):
    SCALAR = "scalar"           # 单值（如"解决率 85%"）
    DISTRIBUTION = "distribution"  # 分布（如"按状态分布"）
    TREND = "trend"             # 趋势（如"新增按天趋势"）
    LIST = "list"               # 明细列表


# ── 指标定义 ──────────────────────────────────────────────────


@dataclass
class MetricDef:
    """单个指标的定义。"""

    key: str                              # 唯一标识，如 "ticket.resolve_rate"
    dimension: MetricDimension
    label: str                            # 中文名称，如 "工单解决率"
    desc: str                             # 简短说明，供 LLM 选择指标
    requires_time_range: bool = True
    output_type: MetricOutputType = MetricOutputType.SCALAR
    collect_fn: str = ""                  # ReportDataCollector 上的采集方法名


# ── 指标目录 ──────────────────────────────────────────────────


@dataclass
class _DimensionGroup:
    """一个维度的指标集合。"""
    label: str
    table_name: str                       # 对应数据库表名（提示用）
    metrics: list[MetricDef] = field(default_factory=list)


# 注意：指标ably 以"对象.指标"的形式命名，如 "ticket.resolve_rate"
# 采集函数名拼接规则：_{dimension}_{metric}_metric，如 _ticket_resolve_rate_metric

_TICKET_METRICS: list[MetricDef] = [
    MetricDef(
        key="ticket.total",
        dimension=MetricDimension.TICKET,
        label="工单总数",
        desc="全部工单的总数量（不限时间范围）",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_total_metric",
    ),
    MetricDef(
        key="ticket.new_count",
        dimension=MetricDimension.TICKET,
        label="新增工单数",
        desc="指定时间范围内新增的工单数量",
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_new_count_metric",
    ),
    MetricDef(
        key="ticket.resolved_count",
        dimension=MetricDimension.TICKET,
        label="已解决工单数",
        desc="指定时间范围内解决（resolved）的工单数量",
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_resolved_count_metric",
    ),
    MetricDef(
        key="ticket.closed_count",
        dimension=MetricDimension.TICKET,
        label="已关闭工单数",
        desc="指定时间范围内关闭的工单数量",
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_closed_count_metric",
    ),
    MetricDef(
        key="ticket.resolve_rate",
        dimension=MetricDimension.TICKET,
        label="工单解决率",
        desc="已解决+已关闭工单占总数的百分比",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_resolve_rate_metric",
    ),
    MetricDef(
        key="ticket.overdue_count",
        dimension=MetricDimension.TICKET,
        label="逾期工单数",
        desc="截止日期已过但状态未完结的工单数量",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_ticket_overdue_count_metric",
    ),
    MetricDef(
        key="ticket.by_status",
        dimension=MetricDimension.TICKET,
        label="工单状态分布",
        desc="按状态（新建/处理中/待处理/已解决/已关闭/已取消）统计的工单数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_ticket_by_status_metric",
    ),
    MetricDef(
        key="ticket.by_priority",
        dimension=MetricDimension.TICKET,
        label="工单优先级分布",
        desc="按优先级（低/中/高/紧急）统计的工单数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_ticket_by_priority_metric",
    ),
    MetricDef(
        key="ticket.by_type",
        dimension=MetricDimension.TICKET,
        label="工单类型分布",
        desc="按类型（报障/Bug/支持需求/功能需求/其他）统计的工单数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_ticket_by_type_metric",
    ),
    MetricDef(
        key="ticket.new_by_day",
        dimension=MetricDimension.TICKET,
        label="新增工单趋势",
        desc="指定时间范围内每日新增工单数的趋势（按天统计）",
        output_type=MetricOutputType.TREND,
        collect_fn="_ticket_new_by_day_metric",
    ),
    MetricDef(
        key="ticket.overdue_list",
        dimension=MetricDimension.TICKET,
        label="逾期工单明细",
        desc="逾期工单的详细列表（工单ID、标题、状态、截止日期）",
        requires_time_range=False,
        output_type=MetricOutputType.LIST,
        collect_fn="_ticket_overdue_list_metric",
    ),
    MetricDef(
        key="ticket.items",
        dimension=MetricDimension.TICKET,
        label="工单明细",
        desc="指定时间范围内有变更的工单明细列表",
        output_type=MetricOutputType.LIST,
        collect_fn="_ticket_items_metric",
    ),
]

_PROJECT_METRICS: list[MetricDef] = [
    MetricDef(
        key="project.total",
        dimension=MetricDimension.PROJECT,
        label="项目总数",
        desc="交付项目的总数",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_project_total_metric",
    ),
    MetricDef(
        key="project.active_count",
        dimension=MetricDimension.PROJECT,
        label="活跃项目数",
        desc="状态为「进行中」的项目数量",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_project_active_count_metric",
    ),
    MetricDef(
        key="project.completed_count",
        dimension=MetricDimension.PROJECT,
        label="已完成项目数",
        desc="状态为「已完成」或「已关闭」的项目数量",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_project_completed_count_metric",
    ),
    MetricDef(
        key="project.on_hold_count",
        dimension=MetricDimension.PROJECT,
        label="暂停项目数",
        desc="状态为「已暂停」的项目数量",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_project_on_hold_count_metric",
    ),
    MetricDef(
        key="project.by_status",
        dimension=MetricDimension.PROJECT,
        label="项目状态分布",
        desc="按状态统计的项目数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_project_by_status_metric",
    ),
    MetricDef(
        key="project.items",
        dimension=MetricDimension.PROJECT,
        label="项目明细",
        desc="项目详细信息列表（名称、状态、问题数、风险数、成员等）",
        requires_time_range=False,
        output_type=MetricOutputType.LIST,
        collect_fn="_project_items_metric",
    ),
]

_RISK_METRICS: list[MetricDef] = [
    MetricDef(
        key="risk.total",
        dimension=MetricDimension.RISK,
        label="风险总数",
        desc="全部风险的数量",
        requires_time_range=False,
        output_type=MetricOutputType.SCALAR,
        collect_fn="_risk_total_metric",
    ),
    MetricDef(
        key="risk.new_count",
        dimension=MetricDimension.RISK,
        label="新增风险数",
        desc="指定时间范围内新增的风险数量",
        output_type=MetricOutputType.SCALAR,
        collect_fn="_risk_new_count_metric",
    ),
    MetricDef(
        key="risk.closed_count",
        dimension=MetricDimension.RISK,
        label="已关闭风险数",
        desc="指定时间范围内关闭的风险数量",
        output_type=MetricOutputType.SCALAR,
        collect_fn="_risk_closed_count_metric",
    ),
    MetricDef(
        key="risk.by_level",
        dimension=MetricDimension.RISK,
        label="风险等级分布",
        desc="按风险等级统计的风险数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_risk_by_level_metric",
    ),
    MetricDef(
        key="risk.by_status",
        dimension=MetricDimension.RISK,
        label="风险状态分布",
        desc="按状态（未关闭/已关闭）统计的风险数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_risk_by_status_metric",
    ),
    MetricDef(
        key="risk.by_category",
        dimension=MetricDimension.RISK,
        label="风险分类分布",
        desc="按风险类别统计的风险数量分布",
        requires_time_range=False,
        output_type=MetricOutputType.DISTRIBUTION,
        collect_fn="_risk_by_category_metric",
    ),
    MetricDef(
        key="risk.items",
        dimension=MetricDimension.RISK,
        label="风险明细",
        desc="风险详细信息列表（风险代码、等级、分类、负责人、状态）",
        requires_time_range=False,
        output_type=MetricOutputType.LIST,
        collect_fn="_risk_items_metric",
    ),
]

# ── 维度分组 ──────────────────────────────────────────────────

DIMENSION_GROUPS: dict[str, _DimensionGroup] = {
    "ticket": _DimensionGroup(
        label="工单",
        table_name="tasks",
        metrics=_TICKET_METRICS,
    ),
    "project": _DimensionGroup(
        label="项目",
        table_name="project",
        metrics=_PROJECT_METRICS,
    ),
    "risk": _DimensionGroup(
        label="风险",
        table_name="risk",
        metrics=_RISK_METRICS,
    ),
}

# ── 扁平化 key → MetricDef 索引 ───────────────────────────────

_METRIC_BY_KEY: dict[str, MetricDef] = {}
for _group in DIMENSION_GROUPS.values():
    for _m in _group.metrics:
        _METRIC_BY_KEY[_m.key] = _m


def get_metric_def(key: str) -> MetricDef | None:
    """按 key 获取指标定义。"""
    return _METRIC_BY_KEY.get(key)


def list_metrics_by_dimension(dimension: str | None = None) -> list[MetricDef]:
    """列出指标，可按维度过滤。"""
    if dimension is None:
        return list(_METRIC_BY_KEY.values())
    group = DIMENSION_GROUPS.get(dimension)
    return list(group.metrics) if group else []


# ── LLM prompt 中的指标清单（供阶段 2 的 AnalysisPlanner 使用）──


def catalog_for_llm_prompt() -> str:
    """生成供 LLM 解析指标意图时使用的指标目录文本。

    格式：key | 维度 | 名称 | 说明 | 是否需要时间范围
    """
    lines = ["可查询的分析指标清单："]
    lines.append("| 指标Key | 维度 | 名称 | 说明 | 需时间范围 |")
    lines.append("|---------|------|------|------|-----------|")
    for key in sorted(_METRIC_BY_KEY.keys()):
        m = _METRIC_BY_KEY[key]
        lines.append(
            f"| {m.key} | {m.dimension.value} | {m.label} | "
            f"{m.desc} | {'是' if m.requires_time_range else '否'} |"
        )
    return "\n".join(lines)


# ── 时间范围解析（供 collect_by_plan 使用）─────────────────────

from datetime import date, datetime, timedelta


def resolve_time_range(
    range_type: TimeRangeType,
    target_date: date | None = None,
    days: int = 7,
    start_str: str | None = None,
    end_str: str | None = None,
) -> tuple[datetime, datetime, str]:
    """将时间范围描述解析为具体的 (start, end, label)。

    Args:
        range_type: 时间范围类型。
        target_date: 基准日期，默认今天。
        days: RECENT_DAYS 时的天数。
        start_str / end_str: CUSTOM 时的起止日期 YYYY-MM-DD。

    Returns:
        (start_datetime, end_datetime, 范围描述)
    """
    today = target_date or date.today()

    if range_type == TimeRangeType.TODAY:
        s = datetime.combine(today, datetime.min.time())
        e = datetime.combine(today, datetime.max.time())
        return s, e, today.strftime("%Y-%m-%d")

    if range_type == TimeRangeType.YESTERDAY:
        d = today - timedelta(days=1)
        s = datetime.combine(d, datetime.min.time())
        e = datetime.combine(d, datetime.max.time())
        return s, e, d.strftime("%Y-%m-%d")

    if range_type == TimeRangeType.RECENT_DAYS:
        start = today - timedelta(days=days - 1)
        s = datetime.combine(start, datetime.min.time())
        e = datetime.combine(today, datetime.max.time())
        return s, e, f"{start.strftime('%Y-%m-%d')} ~ {today.strftime('%Y-%m-%d')}"

    if range_type == TimeRangeType.THIS_WEEK:
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        s = datetime.combine(monday, datetime.min.time())
        e = datetime.combine(sunday, datetime.max.time())
        return s, e, f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"

    if range_type == TimeRangeType.LAST_WEEK:
        monday = today - timedelta(days=today.weekday() + 7)
        sunday = monday + timedelta(days=6)
        s = datetime.combine(monday, datetime.min.time())
        e = datetime.combine(sunday, datetime.max.time())
        return s, e, f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"

    if range_type == TimeRangeType.THIS_MONTH:
        first = today.replace(day=1)
        if today.month == 12:
            last = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        s = datetime.combine(first, datetime.min.time())
        e = datetime.combine(last, datetime.max.time())
        return s, e, f"{first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}"

    if range_type == TimeRangeType.LAST_MONTH:
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        if first.month == 12:
            last = first.replace(year=first.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last = first.replace(month=first.month + 1, day=1) - timedelta(days=1)
        s = datetime.combine(first, datetime.min.time())
        e = datetime.combine(last, datetime.max.time())
        return s, e, f"{first.strftime('%Y-%m-%d')} ~ {last.strftime('%Y-%m-%d')}"

    if range_type == TimeRangeType.CUSTOM:
        if not start_str or not end_str:
            raise ValueError("CUSTOM 时间范围需要提供 start_str 和 end_str")
        s = datetime.strptime(start_str, "%Y-%m-%d")
        e = datetime.combine(datetime.strptime(end_str, "%Y-%m-%d").date(), datetime.max.time())
        return s, e, f"{start_str} ~ {end_str}"

    raise ValueError(f"未知时间范围类型: {range_type}")