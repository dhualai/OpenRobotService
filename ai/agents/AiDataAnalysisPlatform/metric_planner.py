"""AI 数据分析平台 · 指标意图解析器

将用户自然语言问题解析为结构化的 :class:`AnalysisPlan`。

两层解析：
1. 快路径：正则/词典，命中明确模板直接出 plan（零 LLM 成本）
2. 慢路径：LLM 结构化输出，prompt 中给出指标目录，要求模型只输出 JSON

解析失败 / 信息不足时的降级策略：
- LLM 输出非法 JSON → 退回快路径结果；快路径也无结果 → 澄清
- 必要字段缺失 → 生成澄清问题（clarify）

多轮澄清：
- 澄清后用户补充回答时，通过 ``previous_plan`` 传入上一轮已解析的 plan，
  本轮命中的字段覆盖上一轮，未命中的保留。

用法::

    planner = AnalysisPlanner(llm_client)
    plan = await planner.parse("最近7天工单解决率怎么样？")
    missing = planner.missing_fields(plan)
    if missing:
        clarify_text, suggestions = planner.build_clarify_questions(plan, missing)
"""

from __future__ import annotations

import json
import re
from datetime import date

from .logging_config import get_logger
from .metric_registry import MetricDimension, catalog_for_llm_prompt, get_metric_def
from .prompts import build_plan_parser_system_prompt, build_plan_parser_user_prompt
from .schemas import AnalysisPlan, ScopeSpec, TimeRangeSpec

logger = get_logger("MetricPlanner")

# ── 快路径：时间词 ──────────────────────────────────────────────

# (匹配模式, 时间类型)
_TIME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(今天|今日)"), "today"),
    (re.compile(r"(昨天|昨日)"), "yesterday"),
    (re.compile(r"(最近|近)\s*(\d+)\s*天"), "recent_days"),
    (re.compile(r"(本周|这周)"), "this_week"),
    (re.compile(r"上周"), "last_week"),
    (re.compile(r"(本月|这个月)"), "this_month"),
    (re.compile(r"(上月|上个月)"), "last_month"),
]

# 绝对日期模式：「9月7号」「从9月7号以后」「9月1号到9月7号」
_ABS_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})[号日]")
_ABS_AFTER_RE = re.compile(r"(\d{1,2})月(\d{1,2})[号日]\s*(?:以后|之后|以来|起|开始)")
_ABS_RANGE_RE = re.compile(
    r"(\d{1,2})月(\d{1,2})[号日]\s*(?:到|至|~|-)\s*(\d{1,2})月(\d{1,2})[号日]"
)


def _extract_time(
    text: str,
) -> tuple[str, bool, int, str | None, str | None, str]:
    """从文本提取时间范围。

    返回 (type, 是否显式提及, 天数, 起始日期 YYYY-MM-DD, 结束日期 YYYY-MM-DD, 中文描述)。
    绝对日期（如「从9月7号以后」）优先于相对时间词，type 为 custom：
    「以后/之后/以来/起」→ start=该日期、end=今天；「到/至」区间 → start=end=两端日期；
    单独日期 → 当天。年份取当前年，若推导日期在未来则回退一年。
    """
    today = date.today()

    def _to_iso(m: str, d: str) -> str:
        try:
            year = today.year
            target = date(year, int(m), int(d))
            if target > today:
                target = date(year - 1, int(m), int(d))
            return target.strftime("%Y-%m-%d")
        except ValueError:
            return today.strftime("%Y-%m-%d")

    # 「9月1号到9月7号」区间
    rng = _ABS_RANGE_RE.search(text)
    if rng:
        start_iso = _to_iso(rng.group(1), rng.group(2))
        end_iso = _to_iso(rng.group(3), rng.group(4))
        label = f"{rng.group(1)}月{rng.group(2)}号 至 {rng.group(3)}月{rng.group(4)}号"
        return "custom", True, 7, start_iso, end_iso, label

    # 「从9月7号以后/之后/以来/起/开始」
    after = _ABS_AFTER_RE.search(text)
    if after:
        start_iso = _to_iso(after.group(1), after.group(2))
        label = f"{after.group(1)}月{after.group(2)}号以后"
        return "custom", True, 7, start_iso, today.strftime("%Y-%m-%d"), label

    # 单独日期「9月7号」
    single = _ABS_DATE_RE.search(text)
    if single:
        day_iso = _to_iso(single.group(1), single.group(2))
        label = f"{single.group(1)}月{single.group(2)}号"
        return "custom", True, 7, day_iso, day_iso, label

    for pattern, time_type in _TIME_PATTERNS:
        m = pattern.search(text)
        if m:
            days = 7
            if time_type == "recent_days":
                days = int(m.group(2) or 7)
            return time_type, True, days, None, None, ""
    return "recent_days", False, 7, None, None, ""


# ── 快路径：动作词 ──────────────────────────────────────────────

_ACTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(趋势|走势|变化)"), "trend"),
    (re.compile(r"(分布|占比|构成|比例)"), "distribution"),
    (re.compile(r"(排名|排行|top\s*\d*|前\s*\d+)"), "top"),
    (re.compile(r"(对比|比较|同比|环比)"), "compare"),
]


def _extract_action(text: str) -> str:
    for pattern, action in _ACTION_PATTERNS:
        if pattern.search(text):
            return action
    return "summary"


# ── 快路径：维度 + 指标词 → metric keys ─────────────────────────

# 每个维度一条规则：(正则, 维度名, 指标子规则列表)
# 指标子规则：(正则, 命中时加入的 metric keys)；子规则依次判断，
# 最后一个兜底子规则（正则 None）在无任何命中时生效。
_DIMENSION_RULES: list[tuple[re.Pattern, str, list[tuple[re.Pattern | None, list[str]]]]] = [
    # 搬运效率/机器人组维度（collection_data 表）：放在最前，
    # 保证「搬运任务数量」「总任务数」「任务情况」等所有任务类问题优先命中
    # collection，不被工单「任务」等词抢先。
    # 任务口径统属搬运效率（taskNumber.totalTasks / taskNumber.carry）。
    (
        re.compile(r"(搬运|搬货|AGV|agv|机器人|任务)"),
        "collection",
        [
            # 无数据转投：含「搬运/任务」但语义是「哪些项目无数据/为空」时，
            # 不按 collection 单项目口径分析，转投全局无数据项目清单
            # （project.no_data_items）；「为空」用负向后顾排除「不为空」。
            # 数据词间允许夹入维度词（「没有搬运数据」「没有搬运效率数据」）。
            (re.compile(r"((?:没有|无|没)(?:搬运效率|搬运|机器人|任务|效率){0,2}数据|未上报|没有上报|未采集|没有采集|(?<!不)为空)"), [
                "project.no_data_items",
            ]),
            (re.compile(r"(干预|切手动)"), [
                "collection.avg_manual_switch_count",
                "collection.manual_intervention_rate",
            ]),
            (re.compile(r"故障"), [
                "collection.fault_hours",
                "collection.avg_fault_duration_minutes",
                "collection.items",
            ]),
            (re.compile(r"(各组|组对比|型号|机器人组)"), [
                "collection.robot_group_compare",
                "collection.items",
            ]),
            # 「总任务数」「任务数量」等任务计数口径 → 总任务数 + 搬运任务数量
            # （放在各组之后：各组对比优先）
            (re.compile(r"任务数"), [
                "collection.total_tasks",
                "collection.carry_task_count",
            ]),
            (None, [
                "collection.total_tasks",
                "collection.carry_task_count",
                "collection.effective_work_hours",
                "collection.fault_hours",
                "collection.idle_hours",
                "collection.avg_error_count",
                "collection.avg_fault_duration_minutes",
                "collection.avg_carry_duration_minutes",
                "collection.avg_manual_switch_count",
                "collection.manual_intervention_rate",
                "collection.robot_group_compare",
                "collection.items",
            ]),
        ],
    ),
    (
        # 工单维度：任务类问法已全部归搬运效率维度，这里只保留工单/报障/提单
        re.compile(r"(工单|报障|提单)"),
        "ticket",
        [
            (re.compile(r"(解决率|完成率)"), ["ticket.resolve_rate"]),
            (re.compile(r"逾期"), ["ticket.overdue_count", "ticket.overdue_list"]),
            (re.compile(r"(新增|新建)"), ["ticket.new_count", "ticket.new_by_day"]),
            (None, ["ticket.total", "ticket.new_count", "ticket.by_status", "ticket.resolve_rate"]),
        ],
    ),
    (
        re.compile(r"风险"),
        "risk",
        [
            (re.compile(r"(等级|级别)"), ["risk.by_level"]),
            (re.compile(r"(新增|新建)"), ["risk.new_count"]),
            (re.compile(r"(关闭|闭环)"), ["risk.closed_count"]),
            (re.compile(r"(分类|类别)"), ["risk.by_category"]),
            (None, ["risk.total", "risk.new_count", "risk.by_level", "risk.by_status"]),
        ],
    ),
    (
        re.compile(r"项目"),
        "project",
        [
            (re.compile(r"(活跃|进行中)"), ["project.active_count"]),
            (re.compile(r"(完成|交付)"), ["project.completed_count"]),
            (re.compile(r"(暂停|搁置)"), ["project.on_hold_count"]),
            # 「没有数据」「未上报」等 → 指定时间范围内无采集数据上报的项目清单
            # （同样支持「没有搬运数据」等夹入维度词的问法）
            (re.compile(r"((?:没有|无|没)(?:搬运效率|搬运|机器人|任务|效率){0,2}数据|未上报|没有上报|未采集|没有采集)"),
             ["project.no_data_items"]),
            # 「哪些项目」「有什么项目」「项目列表/清单/明细」→ 项目明细清单
            # （名称/状态等逐项展示；放在 no_data 之后，保证「哪些项目没有
            # 数据」仍优先命中无数据清单）
            (re.compile(r"(哪些|有什么|什么|列表|清单|明细)"), ["project.items"]),
            (None, ["project.total", "project.active_count", "project.by_status"]),
        ],
    ),
]


def _project_hint_from_text(text: str) -> str | None:
    """从文本提取「XX项目」形式的项目名线索（澄清轮补充回答识别用）。"""
    m = re.search(r"([^，,。.!！？?\s]{2,16})项目", text)
    if m:
        return m.group(1).strip()
    return None


def _dimension_hit(text: str) -> bool:
    """文本是否命中任一维度词（用于区分项目补充回答与指标提问）。"""
    return any(pat.search(text) for pat, _, _ in _DIMENSION_RULES)


def _plan_missing_fields(plan: AnalysisPlan) -> list[str]:
    """plan 完整性校验（AnalysisPlanner.missing_fields 的模块级实现）。

    搬运效率（collection_data）维度必须明确到单个项目：多项目混合时
    标量卡片取窗口内最新一条记录，口径不清晰，故强制确认项目。
    """
    missing: list[str] = []

    if not plan.metric_keys:
        missing.append("metric_keys")

    has_time_sensitive = any(
        (m := get_metric_def(k)) is not None and m.requires_time_range
        for k in plan.metric_keys
    )
    if has_time_sensitive and not plan.time_range.explicit:
        missing.append("time_range")

    has_collection = any(
        (m := get_metric_def(k)) is not None
        and m.dimension == MetricDimension.COLLECTION
        for k in plan.metric_keys
    )
    if has_collection and plan.scope.type != "single_project":
        missing.append("project_code")

    if plan.scope.type == "single_project" and not plan.scope.project_code:
        missing.append("project_code")

    return missing


def _fast_path_parse(
    text: str, previous_plan: AnalysisPlan | None = None
) -> AnalysisPlan | None:
    """快路径解析：命中明确模板时返回 plan，否则返回 None（交给 LLM）。

    仅命中时间词/动作词而无维度词时，也返回带空 metric_keys 的 plan，
    供多轮澄清合并使用。

    项目补充回答：上一轮缺失 project_code 时，本轮「XX项目」类回复视为
    范围补充，不解析项目维度兜底指标（避免覆盖上一轮已解析的 metric_keys）。
    """
    time_type, time_explicit, days, start_date, end_date, time_label = _extract_time(text)
    action = _extract_action(text)

    metric_keys: list[str] = []
    matched_dim: str | None = None

    # 项目补充回答识别：上一轮缺 project_code 且本轮文本含「XX项目」形式
    project_supplement: ScopeSpec | None = None
    if previous_plan is not None and "project_code" in _plan_missing_fields(previous_plan):
        hint = _project_hint_from_text(text)
        if hint is None and not time_explicit and action == "summary" \
                and not _dimension_hit(text):
            # 消歧候选按钮发送的完整项目名可能不含「项目」后缀（如「LST-CAT」）：
            # 无维度/时间/动作词的短文本整体视为项目补充，交给查库映射收敛。
            # 只取换行前第一段（question 部分），避免拼接的页面场景上下文混入
            stripped = text.strip().split("\n")[0].strip()
            if 2 <= len(stripped) <= 30:
                hint = stripped
        if hint:
            project_supplement = ScopeSpec(type="single_project", project_name=hint)

    if project_supplement is None:
        for dim_pattern, dim_name, sub_rules in _DIMENSION_RULES:
            if not dim_pattern.search(text):
                continue
            matched_dim = dim_name
            for rule_pattern, keys in sub_rules:
                if rule_pattern is None or rule_pattern.search(text):
                    metric_keys.extend(keys)
                    break
            # 命中一个维度即停止（问题通常针对单一维度）
            break

    # 动作补丁：仅在维度命中时按维度补充对应指标
    if metric_keys and action == "trend" and matched_dim == "ticket" \
            and not any(k.endswith("_by_day") for k in metric_keys):
        metric_keys.append("ticket.new_by_day")
    if metric_keys and action == "trend" and matched_dim == "collection" \
            and "collection.items" not in metric_keys:
        metric_keys.append("collection.items")
    if metric_keys and action == "distribution":
        if matched_dim == "ticket" \
                and not any(k.startswith("ticket.by_") for k in metric_keys):
            metric_keys.extend(["ticket.by_status", "ticket.by_type"])
        elif matched_dim == "risk" \
                and not any(k.startswith("risk.by_") for k in metric_keys):
            metric_keys.extend(["risk.by_level", "risk.by_status"])
        elif matched_dim == "project" \
                and not any(k.startswith("project.by_") for k in metric_keys):
            metric_keys.append("project.by_status")

    # 完全无命中（无维度、无时间、无动作）→ None 交给 LLM
    # 项目补充回答（supplement）即使无指标/时间也须保留：否则澄清轮回复
    # 「XX项目」会被误判为无命中而走 LLM 慢路径，导致反复要求确认项目
    if not metric_keys and not time_explicit and action == "summary" \
            and project_supplement is None:
        return None

    # 去重保序
    seen: set[str] = set()
    unique_keys = [k for k in metric_keys if not (k in seen or seen.add(k))]

    return AnalysisPlan(
        metric_keys=unique_keys,
        time_range=TimeRangeSpec(
            type=time_type,
            days=days,
            start=start_date,
            end=end_date,
            label=time_label,
            explicit=time_explicit,
        ),
        scope=project_supplement or ScopeSpec(type="global"),
        action=action,
        confidence=0.9,
        original_question=text,
    )


# ── LLM 慢路径 JSON 解析 ────────────────────────────────────────


def _extract_json_object(raw: str) -> dict | None:
    """容错提取 LLM 回复中的 JSON 对象。"""
    text = raw.strip()
    if not text:
        return None
    # 去掉可能的 Markdown 代码块包裹
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _plan_from_llm_json(obj: dict, question: str) -> AnalysisPlan:
    """将 LLM 输出的 JSON 字典转换为 AnalysisPlan（含校验与兜底）。"""
    # 指标 key 校验：仅保留注册表中存在的
    raw_keys = obj.get("metric_keys") or []
    metric_keys = [k for k in raw_keys if isinstance(k, str) and get_metric_def(k)]

    time_type = obj.get("time_range_type") or "recent_days"
    if time_type not in (
        "today", "yesterday", "recent_days", "this_week", "last_week",
        "this_month", "last_month", "custom",
    ):
        time_type = "recent_days"
    days = obj.get("time_days") or 7
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 7

    action = obj.get("action") or "summary"
    if action not in ("summary", "trend", "distribution", "compare", "top"):
        action = "summary"

    scope_type = obj.get("scope_type") or "global"
    if scope_type not in ("global", "single_project", "user_projects"):
        scope_type = "global"

    confidence = obj.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))

    return AnalysisPlan(
        metric_keys=metric_keys,
        time_range=TimeRangeSpec(
            type=time_type,
            days=days,
            start=obj.get("time_start"),
            end=obj.get("time_end"),
            explicit=bool(obj.get("time_mentioned")),
        ),
        scope=ScopeSpec(
            type=scope_type,
            project_code=obj.get("project_code") or None,
            project_name=obj.get("project_name") or None,
        ),
        action=action,
        confidence=confidence,
        original_question=question,
    )


# ── 澄清问题 ────────────────────────────────────────────────────

_CLARIFY_TEMPLATES: dict[str, tuple[str, list[str]]] = {
    "metric_keys": (
        "您想了解哪方面的数据指标？我目前支持工单、风险、项目、搬运效率四个维度的统计。",
        ["工单解决率", "逾期工单", "风险等级分布", "项目进展", "搬运效率"],
    ),
    "time_range": (
        "请补充统计的时间范围。",
        ["今天", "近7天", "本周", "本月"],
    ),
    "project_code": (
        "请确认要分析的项目名称或项目代码。",
        [],
    ),
}


# ── 多轮澄清会话缓存 ────────────────────────────────────────────


class PlanConversationCache:
    """澄清会话的 plan 缓存（进程内存，按 conversation_id 索引）。

    仅用于澄清多轮：保存待补充的 plan，补充完成后删除。
    """

    def __init__(self, max_size: int = 500) -> None:
        self._store: dict[str, AnalysisPlan] = {}
        self._max_size = max_size

    def get(self, conversation_id: str | None) -> AnalysisPlan | None:
        if not conversation_id:
            return None
        return self._store.get(conversation_id)

    def put(self, conversation_id: str | None, plan: AnalysisPlan) -> None:
        if not conversation_id:
            return
        if len(self._store) >= self._max_size:
            # 简单淘汰：删除最早插入的一半
            for key in list(self._store.keys())[: self._max_size // 2]:
                self._store.pop(key, None)
        self._store[conversation_id] = plan

    def delete(self, conversation_id: str | None) -> None:
        if conversation_id:
            self._store.pop(conversation_id, None)


# ── 解析器 ──────────────────────────────────────────────────────


class AnalysisPlanner:
    """指标意图解析器：问题文本 → AnalysisPlan。"""

    def __init__(self, llm_client) -> None:
        self._llm = llm_client

    async def parse(
        self,
        question: str,
        context: str | None = None,
        previous_plan: AnalysisPlan | None = None,
    ) -> AnalysisPlan:
        """解析用户问题为分析计划。

        Args:
            question: 用户问题。
            context: 补充上下文（如页面场景、项目名）。
            previous_plan: 上一轮澄清会话中的 plan（多轮补充时传入）。

        Returns:
            AnalysisPlan；无法判断为数据分析时 metric_keys 为空。
        """
        text = "\n".join(
            part.strip() for part in [question, context or ""] if part
        ).strip()
        if not text:
            return AnalysisPlan(original_question=question)

        fast = _fast_path_parse(text, previous_plan)
        if fast is not None:
            return self._merge_with_previous(fast, previous_plan)

        # 慢路径：LLM 结构化输出
        try:
            system_prompt = build_plan_parser_system_prompt()
            user_prompt = build_plan_parser_user_prompt(
                question=question,
                context=context,
                previous_plan=previous_plan,
            )
            raw, _ = await self._llm.chat(
                system_prompt, user_prompt, temperature=0.1, max_tokens=800
            )
            obj = _extract_json_object(raw)
            if obj is None:
                logger.warning("LLM 输出非 JSON，降级为空计划: %r", raw[:200])
                return self._merge_with_previous(
                    AnalysisPlan(original_question=question), previous_plan
                )
            plan = _plan_from_llm_json(obj, question)
            return self._merge_with_previous(plan, previous_plan)
        except Exception as exc:
            logger.exception("LLM 指标解析失败")
            return self._merge_with_previous(
                AnalysisPlan(original_question=question), previous_plan
            )

    @staticmethod
    def _merge_with_previous(
        current: AnalysisPlan | None, previous: AnalysisPlan | None
    ) -> AnalysisPlan:
        """多轮澄清合并：本轮命中的字段覆盖，未命中的保留上一轮。"""
        if previous is None:
            return current or AnalysisPlan()

        merged = previous.model_copy(deep=True)

        if current is None:
            return merged

        if current.metric_keys:
            merged.metric_keys = current.metric_keys
        if current.time_range.explicit:
            merged.time_range = current.time_range
        if current.action != "summary":
            merged.action = current.action
        if current.scope.type != "global":
            merged.scope = current.scope
        # 置信度取本轮
        merged.confidence = current.confidence
        merged.original_question = current.original_question or previous.original_question
        return merged

    # ── 完整性校验与澄清 ──────────────────────────────────────

    @staticmethod
    def missing_fields(plan: AnalysisPlan) -> list[str]:
        """校验 plan 是否可直接执行，返回缺失的必要字段。"""
        return _plan_missing_fields(plan)

    @staticmethod
    def build_clarify_questions(
        plan: AnalysisPlan, missing: list[str]
    ) -> tuple[str, list[str]]:
        """根据缺失字段生成澄清话术与候选项。"""
        parts: list[str] = []
        suggestions: list[str] = []

        for field in missing:
            template = _CLARIFY_TEMPLATES.get(field)
            if template is None:
                continue
            question_text, options = template
            parts.append(question_text)
            suggestions.extend(options)

        return " ".join(parts), suggestions

    @staticmethod
    def is_analysis_intent(plan: AnalysisPlan) -> bool:
        """判断解析结果是否为数据分析意图。"""
        return bool(plan.metric_keys) or plan.action != "summary"
