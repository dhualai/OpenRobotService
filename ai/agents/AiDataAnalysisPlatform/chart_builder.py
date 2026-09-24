"""AI 数据分析平台 · 图表构建与数据本地化

把 ``ReportDataCollector.collect_by_plan()`` 的采集结果转换为：

1. 前端可直接渲染的图表/指标卡片（:class:`ChartSpec` / :class:`MetricCard`），
   数据全部来自采集结果，LLM 不参与图表数据生成，杜绝编造；
2. 喂给 LLM 的中文化数据（根除英文字段名泄漏问题）。

字段 → 中文映射表（``FIELD_LABEL_MAP``）是 localize 与 sanitize 的唯一来源，
避免两处维护。
"""

from __future__ import annotations

import re

from .metric_registry import MetricDimension, MetricOutputType, get_metric_def
from .schemas import ChartSpec, MetricCard

# ── 字段名 → 中文映射（localize 与 sanitize 共用）──────────────────

# 采集结果内部字段 → 中文。外层维度 key（ticket/project/risk/date_range）
# 与单值常见英文单词（total/items 等）仅用于 localize，不参与 sanitize
# 兜底替换（避免误伤正常英文表述）。
FIELD_LABEL_MAP: dict[str, str] = {
    "date_range": "统计周期",
    "ticket": "工单",
    "project": "项目",
    "risk": "风险",
    "collection": "搬运效率",
    "project_info": "项目信息",
    "total": "总数",
    "new_count": "新增数",
    "resolved_count": "已解决数",
    "closed_count": "已关闭数",
    "resolve_rate": "解决率",
    "overdue_count": "逾期数",
    "active_count": "活跃数",
    "completed_count": "已完成数",
    "on_hold_count": "暂停数",
    "by_status": "状态分布",
    "by_priority": "优先级分布",
    "by_type": "类型分布",
    "by_level": "等级分布",
    "by_category": "分类分布",
    # 风险推算指标（risk_assessor 规则评分：项目信息 + 工单综合推算）
    "assessment": "风险评估明细",
    "assessed_count": "已评估项目数",
    "high_risk_count": "高风险项目数",
    # 报告路径 RiskStats 英文字段（sanitize 兜底，防止泄漏到报告正文）
    "risk_score": "风险分数",
    "risk_level": "风险等级",
    "risk_factors": "风险因素",
    "open_tickets": "未关闭工单数",
    "new_30d_tickets": "近30天新增工单数",
    "agv_count": "AGV数量",
    "agv_model_count": "AGV种类",
    "manual_risk": "人工风险点",
    "new_by_day": "每日新增",
    "overdue_list": "逾期明细",
    "items": "明细列表",
    # 标量指标顺带生成的按天序列（标量配趋势图，图+文字展示）
    "new_count_by_day": "每日新增数",
    "resolved_count_by_day": "每日已解决数",
    "closed_count_by_day": "每日已关闭数",
    # 明细列表顺带生成的分布（LIST 指标配分布图，图+文字展示）
    "items_dist": "明细分布",
    # 项目明细按状态分组（组内截断），「哪些项目」类问法喂 LLM 的分组结构
    "items_by_status": "项目分组明细",
    "items_count": "项目明细总数",
    # collection_data 窗口内按天聚合（日期 → 每日汇总），多日趋势图数据源
    "by_day": "每日汇总",
    # 无数据项目（project 表与 collection_data 上报对比）
    "no_data_list": "无数据项目",
    "no_data_count": "无数据项目数",
    "with_data_count": "有数据项目数",
    # collection_data（搬运效率）汇总字段，口径与搬运效率分析页面一致
    "total_tasks": "总任务数",
    "carry_task_count": "搬运任务数量",
    "effective_work_hours": "有效工作时长(小时)",
    "fault_hours": "机器人故障时长(小时)",
    "idle_hours": "空闲无任务时间(小时)",
    "avg_error_count": "平均错误次数",
    "avg_fault_duration_minutes": "平均单次故障时间(分钟)",
    "avg_carry_duration_minutes": "平均单次搬运任务时间(分钟)",
    "avg_manual_switch_count": "平均切手动次数",
    "manual_intervention_rate": "人工干预率",
    "robot_group_compare": "各组数据对比",
    # 项目信息（project_info_node/value/history/mark 四张表）
    "node_total": "节点总数",
    "global_node_count": "全局模板节点数",
    "custom_node_count": "项目自定义节点数",
    "by_value_type": "值类型分布",
    "fill_rate": "填写率",
    "filled_node_count": "已填字段数",
    "fillable_node_count": "可填字段数",
    "change_count": "变更次数",
    "change_by_day": "每日变更",
    "change_count_by_day": "每日变更次数",
    "change_by_type": "变更类型分布",
    "top_marked_nodes": "关注排行",
    "value_items": "已填字段值明细",
}

# 百分比类指标字段（value 为 0~100 的数值，卡片展示时带 % 单位）
_PERCENT_FIELDS: frozenset[str] = frozenset({"resolve_rate", "manual_intervention_rate", "fill_rate"})

# 小数值标量字段：卡片展示保留小数（str(int()) 会截断有效工作时长等小时数）
_DECIMAL_FIELDS: frozenset[str] = frozenset({
    "effective_work_hours",
    "fault_hours",
    "idle_hours",
    "avg_error_count",
    "avg_fault_duration_minutes",
    "avg_carry_duration_minutes",
    "avg_manual_switch_count",
})

# sanitize 兜底替换白名单：仅替换含下划线的字段名（如 new_count），
# 单词字段（total/items/risk 等）是常见英文词，替换会误伤正常表述。
_SANITIZE_FIELDS: tuple[str, ...] = tuple(
    field for field in FIELD_LABEL_MAP if "_" in field
)


# ── 图表构建 ──────────────────────────────────────────────────────

# 图表风格与后台管理图表（frontend AdminEntries.tsx）对齐：macaron 蓝阶配色，
# 轴/图例文字 #888d8f，轴线 #e8eaea，分割线 #f1f4f4。
_MACARON_BLUE = "#3697c3"
_MACARON_PIE_COLORS = [
    "#227197", "#3697c3", "#51bfee", "#93e0ff", "#7fc6e8",
    "#5aa9cd", "#888d8f", "#c9d4d9", "#3d8ab0", "#c9e7f5",
]
_AXIS_TEXT_STYLE = {"color": "#888d8f", "fontSize": 10}
_AXIS_LINE_STYLE = {"lineStyle": {"color": "#e8eaea"}}
_SPLIT_LINE_STYLE = {"lineStyle": {"color": "#f1f4f4"}}
_LEGEND_STYLE = {
    "bottom": 0,
    "itemWidth": 10,
    "itemHeight": 10,
    "textStyle": {"color": "#888d8f", "fontSize": 10},
}


def build_charts(
    metric_keys: list[str], collected: dict
) -> tuple[list[ChartSpec], list[MetricCard]]:
    """按指标白名单把采集结果转换为图表与卡片。

    Args:
        metric_keys: 本轮的指标 key 列表（与 plan.metric_keys 一致）。
        collected: ``collect_by_plan()`` 的返回 dict。

    Returns:
        (charts, cards)：分布指标 → 图表，单值指标 → 卡片；
        明细列表指标（LIST）不配图不配卡。
    """
    charts: list[ChartSpec] = []
    cards: list[MetricCard] = []

    for key in metric_keys:
        metric = get_metric_def(key)
        if metric is None:
            continue
        dim_data = collected.get(metric.dimension.value) or {}
        field = key.split(".", 1)[1]

        # 标量指标附加每日趋势折线图（图+文字展示）：
        # - collection 维度：维度级 by_day 聚合（day → 汇总 dict）
        # - 其他维度（ticket/risk）：采集器顺带生成的 {field}_by_day（day → 数值）
        trend_source: dict | None = None
        if metric.output_type == MetricOutputType.SCALAR and isinstance(dim_data, dict):
            by_day = dim_data.get("by_day")
            if metric.dimension == MetricDimension.COLLECTION and isinstance(by_day, dict):
                trend_source = {
                    day: summary.get(field)
                    for day, summary in by_day.items()
                    if isinstance(summary, dict) and summary.get(field) is not None
                }
            else:
                per_field = dim_data.get(f"{field}_by_day")
                if isinstance(per_field, dict):
                    trend_source = {
                        day: v for day, v in per_field.items() if v is not None
                    }
        if trend_source and len(trend_source) >= 2:
            trend_chart = _build_trend_chart(f"{metric.label}趋势", trend_source)
            if trend_chart is not None:
                charts.append(trend_chart)

        if field not in dim_data:
            continue
        value = dim_data[field]

        if metric.output_type == MetricOutputType.SCALAR:
            cards.append(_build_card(metric.label, field, value))
        elif metric.output_type == MetricOutputType.DISTRIBUTION and isinstance(value, dict):
            charts.append(_build_distribution_chart(metric.label, value))
        elif metric.output_type == MetricOutputType.TREND and isinstance(value, dict):
            trend_chart = _build_trend_chart(metric.label, value)
            if trend_chart is not None:
                charts.append(trend_chart)
        elif metric.output_type == MetricOutputType.LIST:
            # LIST（明细）：采集器顺带生成 items_dist（按状态/等级等分布）时配分布图，
            # 明细内容本身仍交给 LLM 用文字呈现（图+文字）
            dist = dim_data.get("items_dist") if isinstance(dim_data, dict) else None
            if isinstance(dist, dict) and dist:
                charts.append(_build_distribution_chart(f"{metric.label}分布", dist))

    return charts, cards


def _build_card(label: str, field: str, value) -> MetricCard:
    """单值指标 → 指标卡片。百分比类带 % 单位，计数类为纯数字。"""
    if value is None:
        return MetricCard(label=label, value="-", unit=None, kind="count")
    if field in _PERCENT_FIELDS:
        # 去尾零：85.0 → "85"，90.5 → "90.5"
        return MetricCard(label=label, value=f"{float(value):g}", unit="%", kind="metric")
    if field in _DECIMAL_FIELDS:
        # 小时/分钟/次数类指标保留小数：1.91 → "1.91"，2.0 → "2"
        return MetricCard(label=label, value=f"{float(value):g}", unit=None, kind="metric")
    return MetricCard(label=label, value=str(int(value)), unit=None, kind="count")


def _build_distribution_chart(label: str, dist: dict) -> ChartSpec:
    """分布 dict（键已为中文）→ 饼图（≤6 类）或柱状图（>6 类）。"""
    items = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    if len(labels) <= 6:
        return ChartSpec(
            chart_type="pie",
            title=label,
            option={
                "color": _MACARON_PIE_COLORS,
                "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                "legend": {**_LEGEND_STYLE, "type": "scroll"},
                "series": [
                    {
                        "type": "pie",
                        "radius": ["38%", "62%"],
                        "center": ["50%", "42%"],
                        "label": {"color": "#888d8f", "fontSize": 10, "formatter": "{b} {c}"},
                        "itemStyle": {"borderColor": "#fff", "borderWidth": 2},
                        "data": [{"name": k, "value": v} for k, v in items],
                    }
                ],
            },
        )
    return ChartSpec(
        chart_type="bar",
        title=label,
        option={
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": 8, "right": 8, "top": 24, "bottom": 24, "containLabel": True},
            "xAxis": {
                "type": "category",
                "data": labels,
                "axisTick": {"show": False},
                "axisLine": _AXIS_LINE_STYLE,
                "axisLabel": {"interval": 0, "rotate": 30, **_AXIS_TEXT_STYLE},
            },
            "yAxis": {
                "type": "value",
                "minInterval": 1,
                "splitLine": _SPLIT_LINE_STYLE,
                "axisLabel": _AXIS_TEXT_STYLE,
            },
            "series": [
                {
                    "type": "bar",
                    "data": values,
                    "barMaxWidth": 24,
                    "itemStyle": {"color": _MACARON_BLUE, "borderRadius": [4, 4, 0, 0]},
                }
            ],
        },
    )


def _build_trend_chart(label: str, trend: dict) -> ChartSpec | None:
    """趋势 dict（日期为键）→ 折线图。

    值缺失（None）的日期从序列中剔除；剔除后不足 2 个点时不生成图表。
    y 轴不设 minInterval：小时/比率类小数值指标（如 0.66）需要自适应刻度。
    """
    pairs = sorted(
        ((day, v) for day, v in trend.items() if v is not None),
        key=lambda kv: kv[0],
    )
    if len(pairs) < 2:
        return None
    days = [d for d, _ in pairs]
    values = [v for _, v in pairs]
    return ChartSpec(
        chart_type="line",
        title=label,
        option={
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 8, "right": 8, "top": 24, "bottom": 24, "containLabel": True},
            "xAxis": {
                "type": "category",
                "data": days,
                "axisTick": {"show": False},
                "axisLine": _AXIS_LINE_STYLE,
                "axisLabel": _AXIS_TEXT_STYLE,
            },
            "yAxis": {
                "type": "value",
                "splitLine": _SPLIT_LINE_STYLE,
                "axisLabel": _AXIS_TEXT_STYLE,
            },
            "series": [
                {
                    "type": "line",
                    "data": values,
                    "smooth": True,
                    "symbolSize": 5,
                    "lineStyle": {"width": 2, "color": _MACARON_BLUE},
                    "itemStyle": {"color": _MACARON_BLUE},
                    "areaStyle": {"opacity": 0.12},
                }
            ],
        },
    )


# ── 数据本地化（喂 LLM）────────────────────────────────────────────


def localize_collected_data(collected: dict) -> dict:
    """把采集结果转成中文 key 的 dict，供 LLM 分析使用。

    递归替换 dict 的 key（字段名），value 原样保留；
    list 元素递归处理（items 明细中的中文 key 原样保留）。
    """
    def _translate(value):
        if isinstance(value, dict):
            return {FIELD_LABEL_MAP.get(k, k): _translate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_translate(v) for v in value]
        return value

    return _translate(collected)


def sanitize_field_names(text: str) -> str:
    """兜底替换 LLM 回答中残留的英文字段名（仅含下划线的字段）。

    如 ``new_count`` → ``新增数``、``resolve_rate`` → ``解决率``；
    单词字段（total/items/risk 等）不替换，避免误伤正常英文表述。
    """
    for field in _SANITIZE_FIELDS:
        text = re.sub(rf"\b{re.escape(field)}\b", FIELD_LABEL_MAP[field], text)
    return text
