# -*- coding: utf-8 -*-
"""对话式指标分析 · 评测用例集

标注格式：每条用例为 (question, expected)，expected 只标注参与评分的维度：

- ``metric_keys``：期望指标 key 列表（精确匹配，集合相等）
- ``time_type``：期望时间范围类型（today / recent_days / this_week ...）
- ``time_explicit``：用户是否在问题中明确提到时间
- ``action``：期望分析动作（summary / trend / distribution / top / compare）
- ``scope_type``：期望范围类型（global / single_project / user_projects）
- ``missing``：期望触发澄清的缺失字段（用于澄清用例）

用例分组：

- FAST_PATH_CASES：快路径正则/词典可覆盖，零 LLM 成本，期望精确匹配
- SLOW_PATH_CASES：口语化/语义模糊问题，必须走 LLM 慢路径，
  用 ``expect_contains`` 标注"必须包含"的关键指标（宽松匹配，LLM 允许补充合理指标）
- CLARIFY_CASES：缺必要字段，期望 missing_fields 非空
- MULTI_ROUND_CASES：澄清多轮场景，rounds 为 [(问题, 期望本轮 mode/missing), ...]，
  最终轮验证合并后的完整 plan

被 eval_metric_planner.py（解析评测）与 eval_metric_e2e.py（端到端评测）共同引用。
"""

from __future__ import annotations

# ── 快路径用例（12 条）─────────────────────────────────────────
# 期望与 metric_planner._fast_path_parse 当前实现严格对齐，作为回归基准。

FAST_PATH_CASES: list[dict] = [
    # 时间词 + 指标词
    {
        "question": "最近7天工单解决率怎么样？",
        "metric_keys": ["ticket.resolve_rate"],
        "time_type": "recent_days",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "本周新增了多少工单",
        "metric_keys": ["ticket.new_count", "ticket.new_by_day"],
        "time_type": "this_week",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "今天工单逾期情况",
        "metric_keys": ["ticket.overdue_count", "ticket.overdue_list"],
        "time_type": "today",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "近30天工单解决率",
        "metric_keys": ["ticket.resolve_rate"],
        "time_type": "recent_days",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    # 动作词
    {
        "question": "上个月工单新增趋势",
        "metric_keys": ["ticket.new_count", "ticket.new_by_day"],
        "time_type": "last_month",
        "time_explicit": True,
        "action": "trend",
        "scope_type": "global",
    },
    {
        "question": "风险等级分布",
        "metric_keys": ["risk.by_level"],
        "time_type": "recent_days",
        "time_explicit": False,
        "action": "distribution",
        "scope_type": "global",
    },
    {
        "question": "工单状态分布",
        # 快路径兜底规则命中 + distribution 补丁不重复追加（已含 by_status）
        "metric_keys": [
            "ticket.total",
            "ticket.new_count",
            "ticket.by_status",
            "ticket.resolve_rate",
        ],
        "time_type": "recent_days",
        "time_explicit": False,
        "action": "distribution",
        "scope_type": "global",
    },
    # 项目维度
    {
        "question": "项目活跃数量",
        "metric_keys": ["project.active_count"],
        "time_type": "recent_days",
        "time_explicit": False,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "本月完成了多少项目",
        "metric_keys": ["project.completed_count"],
        "time_type": "this_month",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    # 风险维度
    {
        "question": "今天新增了多少风险",
        "metric_keys": ["risk.new_count"],
        "time_type": "today",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "本周关闭了多少风险",
        "metric_keys": ["risk.closed_count"],
        "time_type": "this_week",
        "time_explicit": True,
        "action": "summary",
        "scope_type": "global",
    },
    {
        "question": "风险分类分布",
        # 快路径："分类|类别" 子规则命中 by_category
        "metric_keys": ["risk.by_category"],
        "time_type": "recent_days",
        "time_explicit": False,
        "action": "distribution",
        "scope_type": "global",
    },
]

# ── LLM 慢路径用例（6 条）───────────────────────────────────────
# 口语化/语义模糊，快路径无精确模板；用 expect_contains 宽松标注关键指标。

SLOW_PATH_CASES: list[dict] = [
    {
        "question": "工单优先级分布",
        "expect_contains": ["ticket.by_priority"],
        "action": "distribution",
    },
    {
        "question": "有多少待处理的报障",
        "expect_contains": ["ticket.by_status"],
    },
    {
        "question": "暂停的项目有多少",
        "expect_contains": ["project.on_hold_count"],
    },
    {
        "question": "这个月交付了哪些项目",
        "expect_contains": ["project.completed_count", "project.items"],
        "time_explicit": True,
    },
    {
        "question": "高等级风险都有哪些",
        "expect_contains": ["risk.by_level", "risk.items"],
    },
    {
        "question": "最近哪个项目问题最多",
        "expect_contains": ["project.items"],
        "time_explicit": True,
    },
]

# ── 澄清用例（4 条）────────────────────────────────────────────
# 解析出指标但缺必要字段（如时间范围），期望 missing_fields 非空。

CLARIFY_CASES: list[dict] = [
    {
        "question": "工单新增情况",
        "metric_keys": ["ticket.new_count", "ticket.new_by_day"],
        "missing": ["time_range"],
    },
    {
        "question": "新增风险情况",
        "metric_keys": ["risk.new_count"],
        "missing": ["time_range"],
    },
    {
        "question": "风险关闭情况",
        "metric_keys": ["risk.closed_count"],
        "missing": ["time_range"],
    },
    {
        # 解决率不要求时间范围 → 不应澄清（对照组）
        "question": "工单解决率怎么样？",
        "metric_keys": ["ticket.resolve_rate"],
        "missing": [],
    },
]

# ── 多轮澄清用例（2 条）────────────────────────────────────────
# rounds: [(问题, 期望 mode, 期望 missing), ...]
# 最后一轮的 plan 必须合并出前几轮已确认的字段。

MULTI_ROUND_CASES: list[dict] = [
    {
        "name": "缺时间 → 补充近7天",
        "rounds": [
            ("工单新增情况", "clarify", ["time_range"]),
            ("近7天", "analysis", []),
        ],
        "final": {
            "metric_keys": ["ticket.new_count", "ticket.new_by_day"],
            "time_type": "recent_days",
            "time_explicit": True,
            "action": "summary",
        },
    },
    {
        "name": "缺时间 → 补充本周",
        "rounds": [
            ("新增风险情况", "clarify", ["time_range"]),
            ("本周", "analysis", []),
        ],
        "final": {
            "metric_keys": ["risk.new_count"],
            "time_type": "this_week",
            "time_explicit": True,
            "action": "summary",
        },
    },
]
