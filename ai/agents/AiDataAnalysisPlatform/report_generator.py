"""AI 数据分析平台 · 日报/周报生成器

从 MySQL 采集项目、风险、工单数据（工单数据源为 tasks 表），调用 LLM 生成结构化日报/周报。

用法::

    # 手动调用（API 或脚本，项目必选）
    from ai.agents.AiDataAnalysisPlatform.report_generator import generate_report

    result = await generate_report(period="daily", date="2026-07-20", project_code="PROJ001")

    # 定时任务调用（APScheduler / cron，同样需指定项目）
    result = await generate_report(period="weekly", date="2026-07-20", project_code="PROJ001")
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import AsyncIterator

from sqlalchemy import func as sa_func, or_ as sa_or

from ai.core.database import (
    SessionLocal,
    Task,
    ProjectDelivery,
    User,
    UserProjectRole,
    CollectionData,
    ProjectInfoNode,
    ProjectInfoValue,
    ProjectInfoValueHistory,
    ProjectInfoNodeMark,
)

from .llm_client import LLMClient
from .config import AnalysisConfig
from .logging_config import get_logger
from .report_prompts import build_report_system_prompt, build_report_user_prompt
from .report_schemas import (
    ReportPeriod,
    ReportRequest,
    ReportResult,
    ReportSection,
    ProjectStats,
    RiskStats,
    TicketStats,
    CollectedData,
)
from .risk_assessor import ProjectRiskSignals, assess_projects

logger = get_logger("ReportGenerator")


# ── 枚举值中文化 ──────────────────────────────────────────────────
# 数据库存储的英文枚举值在采集阶段统一转换为中文标签，
# 避免 LLM 在报告正文中透出 IN_PROGRESS / CLOSED 等原始枚举。

_TICKET_STATUS_CN = {
    "new": "待处理",
    "in_progress": "处理中",
    "pending": "已挂起",
    "resolved": "已解决",
    "canceled": "已取消",
    "cancelled": "已取消",
    "closed": "已关闭",
}

_TICKET_PRIORITY_CN = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "urgent": "紧急",
}

_TICKET_TYPE_CN = {
    "problem": "报障",
    "bug": "Bug",
    "support": "支持需求",
    "feature": "功能需求",
    "other": "其他",
}

_PROJECT_STATUS_CN = {
    "active": "进行中",
    "completed": "已完成",
    "done": "已完成",
    "closed": "已关闭",
    "on_hold": "已暂停",
    "paused": "已暂停",
    "suspended": "已暂停",
}

# 项目明细（project.items）按状态分组后，每组最多列出的项目数；
# 超出部分只计数量（已截断标记），避免全量明细喂 LLM 撑爆上下文。
_PROJECT_ITEMS_PER_GROUP_LIMIT = 30

# ── 风险推算节点定义（风险口径改造：不再读 risk 表）─────────────
# 风险改为从项目信息节点 + 工单数据按规则推算（risk_assessor.py）。
# 节点 key 与 backend info_node_seed_service.TITLE_KEY_MAP 对齐：
# - 总车数 = AGV 数量；车型1/车型2 及各自「数量」= AGV 种类
# - 项目类型（基础信息 > 项目类型，select）
# - 风险点（项目特性 > 风险点，select，人工标记）
_RISK_NODE_KEYS: dict[str, str] = {
    "project_type": "base.project_type",
    "agv_total": "hardware.vehicle.total_count",
    "model_1": "hardware.vehicle.model_1",
    "model_1_qty": "hardware.vehicle.model_1.quantity",
    "model_2": "hardware.vehicle.model_2",
    "model_2_qty": "hardware.vehicle.model_2.quantity",
    "manual_risk": "project_feature.risk",
}

# 近30天新增工单的统计窗口（天）
_RISK_NEW_TICKET_WINDOW_DAYS = 30

# 风险评估明细最多喂 LLM 的项目数（按分数降序截断）
_RISK_ITEMS_LIMIT = 50


def _risk_node_value_str(value: object) -> str:
    """节点值 → 字符串：select 存 {"selected": ...}，text 存原生值。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        selected = value.get("selected")
        return str(selected).strip() if selected not in (None, "") else ""
    return str(value).strip()


def _risk_parse_int(value: object) -> int:
    try:
        return max(int(float(str(value).strip())), 0)
    except (TypeError, ValueError):
        return 0


def _risk_select_value(values: dict, project_id: str, id_by_key: dict, slot: str) -> str:
    node_id = id_by_key.get(_RISK_NODE_KEYS.get(slot, ""))
    if not node_id:
        return ""
    return _risk_node_value_str(values.get((project_id, node_id)))


def _agv_count_of(values: dict, project_id: str, id_by_key: dict) -> int:
    """AGV 数量：总车数节点优先，缺失时回退车型1/车型2 数量之和。"""
    total = _risk_parse_int(
        _risk_node_value_str(
            values.get((project_id, id_by_key.get(_RISK_NODE_KEYS["agv_total"])))
        )
    )
    if total > 0:
        return total
    return sum(
        _risk_parse_int(
            _risk_node_value_str(
                values.get((project_id, id_by_key.get(_RISK_NODE_KEYS[slot])))
            )
        )
        for slot in ("model_1_qty", "model_2_qty")
    )


def _agv_model_count_of(values: dict, project_id: str, id_by_key: dict) -> int:
    """AGV 种类：车型1/车型2 中数量 > 0 的车型数；数量缺失但车型已选时也计 1 种。"""
    count = 0
    for model_slot, qty_slot in (("model_1", "model_1_qty"), ("model_2", "model_2_qty")):
        qty = _risk_parse_int(
            _risk_node_value_str(
                values.get((project_id, id_by_key.get(_RISK_NODE_KEYS[qty_slot])))
            )
        )
        selected = _risk_node_value_str(
            values.get((project_id, id_by_key.get(_RISK_NODE_KEYS[model_slot])))
        )
        if qty > 0 or selected:
            count += 1
    return count

# 项目信息变更历史 operation_type → 中文（project_info_value_history 表）
_PROJECT_INFO_OP_CN = {
    "create": "新增值",
    "update": "修改值",
    "delete": "删除值",
    "node_create": "新增节点",
    "node_move": "移动节点",
    "node_rename": "节点改名",
}


def _norm_enum(value: str | None) -> str:
    """归一化枚举值：去空白、去 "TicketStatus." 类前缀、转小写。

    兼容数据库中大小写不一或带枚举类前缀的存储格式。
    """
    if not value:
        return ""
    v = str(value).strip()
    if "." in v:
        v = v.rsplit(".", 1)[-1]
    return v.lower()


def _cn_label(mapping: dict[str, str], value: str | None, default: str) -> str:
    """将枚举值转换为中文标签；未收录的值（含已是中文）原样返回。"""
    key = _norm_enum(value)
    if not key:
        return default
    return mapping.get(key, str(value).strip())


def _fmt_solve_duration(
    created: datetime | None, resolved: datetime | None
) -> str | None:
    """工单从创建到解决的中文耗时（如「3小时25分钟」「1天4小时」）。

    数据异常（缺创建/解决时间或解决早于创建）时返回 None，由上层置空。
    """
    if not created or not resolved or resolved < created:
        return None
    seconds = int((resolved - created).total_seconds())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours > 0:
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    return f"{minutes}分钟"


def _norm_settlement_period(value: str | None) -> str:
    """归一化业绩核算期 settlement_period：'2026-08' / '2026-8' → '202608'；异常原样返回。

    project.settlement_period 为手工填写（常见 YYYYMM 如 202608，兼容 YYYY-MM），
    是项目维度的时间口径：用户提及时间时按该字段的月份过滤项目。
    """
    if not value:
        return ""
    v = str(value).strip()
    m = re.match(r"^(\d{4})\s*[-/.]?\s*(\d{1,2})$", v)
    if not m:
        return v
    try:
        month = int(m.group(2))
        if not 1 <= month <= 12:
            return v
        return f"{m.group(1)}{month:02d}"
    except ValueError:
        return v


def _settlement_month_keys(start: datetime, end: datetime) -> set[str]:
    """时间窗口 [start, end] 覆盖到的月份集合（YYYYMM），用于 settlement_period 过滤。"""
    months: set[str] = set()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.add(f"{y}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


# ── 采集数据（collection_data）解析 ──────────────────────────────
# 字段口径与搬运效率分析页面一致：
# backend/app/modules/admin/services/transport_efficiency_service.py
# 的 _find_metrics / _metrics_to_summary_and_robots / _parse_rate。

# collection_data 中搬运效率数据的 indicator 标签（数据导入落库时统一改名）
COLLECTION_EFFICIENCY_INDICATOR = "GroupEfficiency"


def _find_group_efficiency_metrics(obj, depth: int = 0) -> dict | None:
    """在 collection_data 的 data JSON 中递归查找 GroupEfficiency 指标对象。

    数据包结构：{data: [{data: [metrics], ...}], start_time, end_time}，
    指标对象以 effectWorkTime 或 dataIndicators 字段为标识。
    """
    if depth > 6:
        return None
    if isinstance(obj, dict):
        if "effectWorkTime" in obj or "dataIndicators" in obj:
            return obj
        for value in obj.values():
            found = _find_group_efficiency_metrics(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_group_efficiency_metrics(value, depth + 1)
            if found:
                return found
    return None


def _parse_rate_text(value) -> float | None:
    """人工干预率解析为百分比数值（0~100），供卡片按 % 展示。

    数据源为 "10.0%" 之类百分比字符串时转数值；已是数值则视为百分比原样返回。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip()
    try:
        num = float(text.rstrip("%"))
    except ValueError:
        return None
    return round(num, 2)


def _compute_collection_summary_and_robots(metrics: dict) -> tuple[dict, list[dict]]:
    """把 GroupEfficiency 指标对象转换为搬运效率汇总 + 各组数据对比。

    计算逻辑与搬运效率分析页面（transport_efficiency_service）一致。
    """
    task_number = (metrics.get("dataIndicators") or {}).get("taskNumber") or {}

    effect_work_time = metrics.get("effectWorkTime") or {}
    robot_error_time = metrics.get("robotErrorTime") or {}
    no_work_time = metrics.get("noWorkTime") or {}
    per_error_time = metrics.get("perErrorTime") or []
    average_carry_time = metrics.get("averageCarryTime") or []

    groups = list(effect_work_time.keys())
    group_count = len(groups) or 1

    total_effect = sum(
        (effect_work_time.get(g) or {}).get("diffTimeSeconds", 0) or 0 for g in groups
    )
    total_fault = sum(
        (robot_error_time.get(g) or {}).get("totalTimeSeconds", 0) or 0 for g in groups
    )
    total_idle = sum(
        (no_work_time.get(g) or {}).get("totalIdleTimeSeconds", 0) or 0 for g in groups
    )
    total_error_num = sum((e.get("errorNum") or 0) for e in per_error_time)
    total_per_error = sum((e.get("perErrorTimeSeconds") or 0) for e in per_error_time)
    total_per_carry = sum(
        (c.get("perGroupSingleTaskSeconds") or 0) for c in average_carry_time
    )
    carry_len = len(average_carry_time) or 1

    summary = {
        "total_tasks": task_number.get("totalTasks"),
        "carry_task_count": task_number.get("carry"),
        "effective_work_hours": round(total_effect / 3600 / group_count, 2),
        "fault_hours": round(total_fault / 3600 / group_count, 2),
        "idle_hours": round(total_idle / 3600 / group_count, 2),
        "avg_error_count": round(total_error_num / group_count, 2),
        "avg_fault_duration_minutes": round(total_per_error / 60 / group_count, 2),
        "avg_carry_duration_minutes": round(total_per_carry / 60 / carry_len, 2),
        "avg_manual_switch_count": (metrics.get("averageManualCount") or {}).get("averageManualCount"),
        "manual_intervention_rate": _parse_rate_text(
            (metrics.get("rateArtificialIntervention") or {}).get("rateArtificialIntervention")
        ),
    }

    error_map = {e.get("robotGroup"): e for e in per_error_time if e.get("robotGroup")}
    carry_map = {c.get("robotGroup"): c for c in average_carry_time if c.get("robotGroup")}

    robots = []
    for group in groups:
        eff_seconds = (effect_work_time.get(group) or {}).get("diffTimeSeconds") or 0
        fault_seconds = (robot_error_time.get(group) or {}).get("totalTimeSeconds") or 0
        idle_seconds = (no_work_time.get(group) or {}).get("totalIdleTimeSeconds") or 0
        carry_info = carry_map.get(group) or {}
        error_info = error_map.get(group) or {}
        task_count = carry_info.get("taskCount") or 0
        eff_hours = eff_seconds / 3600
        robots.append({
            "机器人组": group,
            "搬运任务总数(个)": task_count,
            "有效工作时长(h)": round(eff_hours, 2),
            "有效搬运效率(小时/个)": round(eff_hours / task_count, 2) if task_count else 0,
            "机器人故障时间(h)": round(fault_seconds / 3600, 2),
            "无工作时间(h)": round(idle_seconds / 3600, 2),
            "平均单次故障(分钟)": round((error_info.get("perErrorTimeSeconds") or 0) / 60, 2),
            "平均单次搬运时间(分钟)": round((carry_info.get("perGroupSingleTaskSeconds") or 0) / 60, 2),
        })

    return summary, robots


# ── 日期工具 ──────────────────────────────────────────────────────

def _parse_date(date_str: str | None) -> date:
    """解析日期字符串，为空时返回今天。"""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"日期格式错误: '{date_str}'，应为 YYYY-MM-DD")


def _get_daily_range(target: date) -> tuple[datetime, datetime]:
    """返回指定日期的 00:00 ~ 23:59:59。"""
    start = datetime.combine(target, datetime.min.time())
    end = datetime.combine(target, datetime.max.time())
    return start, end


def _get_weekly_range(target: date) -> tuple[datetime, datetime]:
    """返回 target 所在周（周一~周日）的 00:00 ~ 23:59:59。"""
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    start = datetime.combine(monday, datetime.min.time())
    end = datetime.combine(sunday, datetime.max.time())
    return start, end


def _resolve_period_range(
    period: ReportPeriod, target_date: date
) -> tuple[datetime, datetime, str]:
    """根据周期和目标日期解析统计时间范围与展示文案。"""
    if period == ReportPeriod.DAILY:
        start, end = _get_daily_range(target_date)
        return start, end, target_date.strftime("%Y-%m-%d")

    start, end = _get_weekly_range(target_date)
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    return start, end, f"{monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}"


# ── 数据采集 ──────────────────────────────────────────────────────

class ReportDataCollector:
    """从 MySQL 采集报告所需的统计数据。

    project_ids 为 None 时不过滤（全局统计），为非空列表时按项目ID过滤。

    表关联逻辑：
        - project.id ↔ user_project_roles.project_id（项目成员）
        - user_project_roles.user_id ↔ users.id（成员姓名展示，取 users.name）
        - project.id ↔ tasks.project_id（项目工单）
    """

    def __init__(self, project_ids: list[str] | None = None) -> None:
        self._project_ids = project_ids

    def _get_db(self):
        return SessionLocal()

    def _get_project_members_map(
        self, db, project_ids: list[str]
    ) -> dict[str, list[str]]:
        """按项目聚合成员姓名。

        关联逻辑：project.id ↔ user_project_roles.project_id，再经
        user_project_roles.user_id ↔ users.id 取 users.name 作为显示
        （姓名为空时降级为 username）。
        """
        if not project_ids:
            return {}
        try:
            rows = (
                db.query(UserProjectRole.project_id, User.name, User.username)
                .join(User, User.id == UserProjectRole.user_id)
                .filter(UserProjectRole.project_id.in_(project_ids))
                .all()
            )
            members: dict[str, list[str]] = {}
            for project_id, name, username in rows:
                members.setdefault(project_id, []).append(name or username)
            return members
        except Exception as exc:  # 关联表不可用时不阻断报告生成
            logger.warning("查询项目成员失败，项目将以空成员列表展示: %s", exc)
            return {}

    # ── 项目数据 ──────────────────────────────────────────────

    def collect_project_data(self) -> ProjectStats:
        """查询 project 表，统计项目状态分布。

        过滤关联：project.id ↔ user_project_roles.project_id（过滤列表
        来自 user_project_roles 解析出的 project_id，即 project.id）。
        """
        db = self._get_db()
        try:
            q = db.query(ProjectDelivery)
            if self._project_ids:
                q = q.filter(ProjectDelivery.id.in_(self._project_ids))

            projects = q.all()
            total = len(projects)
            active = sum(1 for p in projects if _norm_enum(p.status) == "active")
            completed = sum(1 for p in projects if _norm_enum(p.status) in ("completed", "done", "closed"))
            on_hold = sum(1 for p in projects if _norm_enum(p.status) in ("on_hold", "paused", "suspended"))

            # 项目成员：project.id ↔ user_project_roles.project_id ↔ users.id
            members_by_project = self._get_project_members_map(
                db, [p.id for p in projects if p.id]
            )

            items = []
            for p in projects:
                items.append({
                    "项目ID": p.id,
                    "项目代码": p.code,
                    "项目名称": p.name,
                    "状态": _cn_label(_PROJECT_STATUS_CN, p.status, "未知"),
                    "问题数": p.issues,
                    "风险数": p.risks,
                    "部署时间": p.deployment_date,
                    "部署版本": p.deployment_version,
                    "近期交付时间": p.recent_delivery_date,
                    "近期交付内容": p.recent_delivery_content,
                    "最终交付时间": p.final_delivery_date,
                    "任务执行情况": p.task_execution_status,
                    "对接人": p.contact_person,
                    "分类依据": p.category_basis,
                    "成员": members_by_project.get(p.id, []),
                })

            return ProjectStats(
                total=total,
                active=active,
                completed=completed,
                on_hold=on_hold,
                items=items,
            )
        finally:
            db.close()

    # ── 风险数据 ──────────────────────────────────────────────

    def collect_risk_data(
        self, start: datetime, end: datetime
    ) -> RiskStats:
        """按规则推算项目风险（不再读 risk 表）。

        报告路径：统计范围即 collector 的项目范围（日报/周报为单项目）。
        评估口径与对话窗一致：项目信息（AGV 数量/种类、项目类型、人工风险点）
        + 工单（未关闭数、近30天新增数），由 risk_assessor 按 yaml 规则推算。
        """
        db = self._get_db()
        try:
            project_q = db.query(ProjectDelivery)
            if self._project_ids:
                project_q = project_q.filter(ProjectDelivery.id.in_(self._project_ids))
            projects = project_q.all()

            assessments = assess_projects(self._build_risk_signals(db, projects))
            if not assessments:
                return RiskStats(
                    score=0,
                    level="低",
                    open_tickets=0,
                    new_30d_tickets=0,
                    agv_count=0,
                    agv_model_count=0,
                    project_type="",
                    manual_risk="",
                    factors=["无足够数据评估项目风险"],
                )
            item = assessments[0]
            return RiskStats(
                score=item["风险分数"],
                level=item["风险等级"],
                open_tickets=item["未关闭工单数"],
                new_30d_tickets=item["近30天新增工单数"],
                agv_count=item["AGV数量"],
                agv_model_count=item["AGV种类"],
                project_type=item["项目类型"],
                manual_risk=item["人工风险点"],
                factors=item["风险因素"],
            )
        finally:
            db.close()

    # ── 工单数据 ──────────────────────────────────────────────

    def _get_user_name_map(self, db) -> dict[str, str]:
        """查询 users 表，构建 user_id/username -> 姓名的映射。

        关联逻辑：tasks.assigned_to ↔ users.id 取 users.name 作为显示。
        同时以 users.id 和 username 作为键，兼容 assigned_to 存储
        用户ID或用户名两种情况；姓名为空时降级为 username。
        """
        try:
            name_map: dict[str, str] = {}
            for u in db.query(User).all():
                display = u.name or u.username
                name_map[str(u.id)] = display
                if u.username:
                    name_map[str(u.username)] = display
            return name_map
        except Exception as exc:  # 用户表不可用时不阻断报告生成
            logger.warning("查询用户表失败，接收人将以 ID 展示: %s", exc)
            return {}

    def _get_project_name_map(self, db) -> dict[str, str]:
        """查询 project 表，构建 project_id -> 项目名称 的映射。

        关联逻辑：tasks.project_id ↔ project.id，据此统一工单的项目名展示。
        """
        try:
            return {
                str(p.id): p.name
                for p in db.query(ProjectDelivery.id, ProjectDelivery.name).all()
                if p.id
            }
        except Exception as exc:  # 项目表不可用时不阻断报告生成
            logger.warning("查询项目表失败，工单项目名将以 tasks 表字段展示: %s", exc)
            return {}

    def collect_ticket_data(
        self, start: datetime, end: datetime
    ) -> TicketStats:
        """查询 tasks 表（工单表），统计指定时间范围内的工单数据。

        过滤关联：project.id ↔ tasks.project_id。
        """
        db = self._get_db()
        try:
            q = db.query(Task)
            if self._project_ids:
                q = q.filter(Task.project_id.in_(self._project_ids))

            all_tickets = q.all()
            total = len(all_tickets)

            # 用户映射：将 assigned_to 解析为真实姓名
            user_name_map = self._get_user_name_map(db)
            # 项目名映射：tasks.project_id ↔ project.id，优先用 project 表名称展示
            project_name_map = self._get_project_name_map(db)

            new_tickets = 0
            resolved = 0
            closed = 0
            overdue = 0
            by_status: dict[str, int] = {}
            by_priority: dict[str, int] = {}
            by_type: dict[str, int] = {}
            items: list[dict] = []

            today = date.today()

            for t in all_tickets:
                # 状态统计（中文化展示；raw_status 保留原始值用于完结判断）
                raw_status = _norm_enum(t.status) or "unknown"
                status = _cn_label(_TICKET_STATUS_CN, t.status, "未知")
                by_status[status] = by_status.get(status, 0) + 1

                # 优先级统计
                priority = _cn_label(_TICKET_PRIORITY_CN, t.priority, "中")
                by_priority[priority] = by_priority.get(priority, 0) + 1

                # 类型统计
                ttype = _cn_label(_TICKET_TYPE_CN, t.task_type, "其他")
                by_type[ttype] = by_type.get(ttype, 0) + 1

                # 新增工单（created_at 在时间范围内）
                created = t.created_at
                is_new = False
                if created and start <= created <= end:
                    new_tickets += 1
                    is_new = True

                # 已解决 / 已关闭（resolved_at / closed_at 在时间范围内）
                if t.resolved_at and start <= t.resolved_at <= end:
                    resolved += 1
                if t.closed_at and start <= t.closed_at <= end:
                    closed += 1

                # 逾期工单（deadline_at < today 且状态未完结）
                if t.deadline_at and t.deadline_at.date() < today:
                    if raw_status not in ("resolved", "closed", "canceled", "cancelled"):
                        overdue += 1

                # 采集当日有变更的工单明细（最多50条）
                updated = t.updated_at
                if (is_new or (updated and start <= updated <= end)) and len(items) < 50:
                    # 接收人：tasks.assigned_to ↔ users.id 关联取 users.name，为空视为未分配
                    if t.assigned_to:
                        assignee = user_name_map.get(str(t.assigned_to), str(t.assigned_to))
                    else:
                        assignee = "未分配"
                    items.append({
                        "工单ID": t.id,
                        "标题": t.title,
                        "描述": (t.description or "")[:200],
                        "状态": status,
                        "类型": ttype,
                        "优先级": priority,
                        "项目名称": project_name_map.get(str(t.project_id), t.project_name) if t.project_id else t.project_name,
                        "接收人": assignee,
                        "创建时间": created.isoformat() if created else None,
                        "更新时间": updated.isoformat() if updated else None,
                        "解决时间": t.resolved_at.isoformat() if t.resolved_at else None,
                        # 周期内已解决的工单：给出从创建到解决的耗时，供报告展示处理时长
                        "解决耗时": _fmt_solve_duration(created, t.resolved_at)
                        if (t.resolved_at and start <= t.resolved_at <= end)
                        else None,
                    })

            # 工单解决率（全量口径：已解决 + 已关闭 / 总数）
            done = by_status.get("已解决", 0) + by_status.get("已关闭", 0)
            resolve_rate = (done / total * 100) if total > 0 else 0.0

            return TicketStats(
                total=total,
                new_tickets=new_tickets,
                resolved=resolved,
                closed=closed,
                overdue=overdue,
                resolve_rate=round(resolve_rate, 1),
                by_status=by_status,
                by_priority=by_priority,
                by_type=by_type,
                items=items,
            )
        finally:
            db.close()

    # ── 按指标采集（新增）──────────────────────────────────

    def collect_by_plan(
        self,
        metric_keys: list[str],
        start: datetime,
        end: datetime,
        date_range_str: str,
        explicit_time: bool = False,
    ) -> dict:
        """按指标白名单采集数据，只查询所需维度。

        Args:
            metric_keys: 指标 key 列表，如 ["ticket.total", "ticket.resolve_rate"]。
            start: 统计起始时间。
            end: 统计结束时间。
            date_range_str: 时间范围描述。
            explicit_time: 时间是否为用户显式提及。True 时 project 维度指标按
                settlement_period（业绩核算期）月份过滤；False（默认）查全部，
                保持历史口径不变。

        Returns:
            dict，key 为维度名（ticket/project/risk），value 为对应指标数据。
            始终包含 "date_range" 字段。
        """
        from .metric_registry import get_metric_def

        # 按维度分组
        ticket_keys: set[str] = set()
        project_keys: set[str] = set()
        risk_keys: set[str] = set()
        collection_keys: set[str] = set()
        project_info_keys: set[str] = set()

        for key in metric_keys:
            metric = get_metric_def(key)
            if metric is None:
                logger.warning("未知指标 key=%s，跳过", key)
                continue
            if metric.dimension == "ticket":
                ticket_keys.add(key)
            elif metric.dimension == "project":
                project_keys.add(key)
            elif metric.dimension == "risk":
                risk_keys.add(key)
            elif metric.dimension == "collection":
                collection_keys.add(key)
            elif metric.dimension == "project_info":
                project_info_keys.add(key)

        logger.info(
            "按指标采集 ticket=%s project=%s risk=%s collection=%s project_info=%s",
            ticket_keys, project_keys, risk_keys, collection_keys, project_info_keys,
        )

        result: dict = {"date_range": date_range_str}

        if ticket_keys:
            result["ticket"] = self._collect_ticket_metrics(ticket_keys, start, end)
        if project_keys:
            result["project"] = self._collect_project_metrics(
                project_keys, start, end, explicit_time
            )
        if risk_keys:
            result["risk"] = self._collect_risk_metrics(risk_keys, start, end)
        if collection_keys:
            result["collection"] = self._collect_collection_metrics(
                collection_keys, start, end
            )
        if project_info_keys:
            result["project_info"] = self._collect_project_info_metrics(
                project_info_keys, start, end
            )

        return result

    # ── 工单维度指标采集 ────────────────────────────────────

    def _collect_ticket_metrics(
        self, keys: set[str], start: datetime, end: datetime
    ) -> dict:
        """一次性采集所有请求的工单指标。"""
        db = self._get_db()
        try:
            q = db.query(Task)
            if self._project_ids:
                q = q.filter(Task.project_id.in_(self._project_ids))

            all_tickets = q.all()
            result: dict = {}

            # -- 标量指标 --
            if "ticket.total" in keys:
                result["total"] = len(all_tickets)

            if "ticket.new_count" in keys:
                result["new_count"] = sum(
                    1 for t in all_tickets if t.created_at and start <= t.created_at <= end
                )
                # 顺带按天序列：标量指标配趋势图（图+文字展示）
                trend: dict[str, int] = {}
                for t in all_tickets:
                    if t.created_at and start <= t.created_at <= end:
                        day = t.created_at.strftime("%Y-%m-%d")
                        trend[day] = trend.get(day, 0) + 1
                result["new_count_by_day"] = dict(sorted(trend.items()))

            if "ticket.resolved_count" in keys:
                result["resolved_count"] = sum(
                    1 for t in all_tickets if t.resolved_at and start <= t.resolved_at <= end
                )
                # 顺带按天序列：标量指标配趋势图（图+文字展示）
                trend: dict[str, int] = {}
                for t in all_tickets:
                    if t.resolved_at and start <= t.resolved_at <= end:
                        day = t.resolved_at.strftime("%Y-%m-%d")
                        trend[day] = trend.get(day, 0) + 1
                result["resolved_count_by_day"] = dict(sorted(trend.items()))

            if "ticket.closed_count" in keys:
                result["closed_count"] = sum(
                    1 for t in all_tickets if t.closed_at and start <= t.closed_at <= end
                )
                # 顺带按天序列：标量指标配趋势图（图+文字展示）
                trend: dict[str, int] = {}
                for t in all_tickets:
                    if t.closed_at and start <= t.closed_at <= end:
                        day = t.closed_at.strftime("%Y-%m-%d")
                        trend[day] = trend.get(day, 0) + 1
                result["closed_count_by_day"] = dict(sorted(trend.items()))

            if "ticket.resolve_rate" in keys:
                total = len(all_tickets)
                done = sum(
                    1 for t in all_tickets
                    if _norm_enum(t.status) in ("resolved", "closed", "canceled", "cancelled")
                )
                result["resolve_rate"] = round(done / total * 100, 1) if total > 0 else 0.0

            if "ticket.overdue_count" in keys:
                today = date.today()
                result["overdue_count"] = sum(
                    1 for t in all_tickets
                    if t.deadline_at and t.deadline_at.date() < today
                    and _norm_enum(t.status) not in ("resolved", "closed", "canceled", "cancelled")
                )

            # -- 分布指标 --
            if "ticket.by_status" in keys:
                dist: dict[str, int] = {}
                for t in all_tickets:
                    label = _cn_label(_TICKET_STATUS_CN, t.status, "未知")
                    dist[label] = dist.get(label, 0) + 1
                result["by_status"] = dist

            if "ticket.by_priority" in keys:
                dist: dict[str, int] = {}
                for t in all_tickets:
                    label = _cn_label(_TICKET_PRIORITY_CN, t.priority, "中")
                    dist[label] = dist.get(label, 0) + 1
                result["by_priority"] = dist

            if "ticket.by_type" in keys:
                dist: dict[str, int] = {}
                for t in all_tickets:
                    label = _cn_label(_TICKET_TYPE_CN, t.task_type, "其他")
                    dist[label] = dist.get(label, 0) + 1
                result["by_type"] = dist

            # -- 趋势指标 --
            if "ticket.new_by_day" in keys:
                trend: dict[str, int] = {}
                for t in all_tickets:
                    if t.created_at and start <= t.created_at <= end:
                        day = t.created_at.strftime("%Y-%m-%d")
                        trend[day] = trend.get(day, 0) + 1
                result["new_by_day"] = dict(sorted(trend.items()))

            # -- 列表指标 --
            if "ticket.overdue_list" in keys:
                today = date.today()
                overdue_items = []
                for t in all_tickets:
                    if t.deadline_at and t.deadline_at.date() < today:
                        raw = _norm_enum(t.status)
                        if raw not in ("resolved", "closed", "canceled", "cancelled"):
                            overdue_items.append({
                                "工单ID": t.id,
                                "标题": t.title,
                                "状态": _cn_label(_TICKET_STATUS_CN, t.status, "未知"),
                                "截止日期": t.deadline_at.isoformat() if t.deadline_at else None,
                                "项目名称": t.project_name,
                            })
                result["overdue_list"] = overdue_items[:50]
                # 顺带按状态分布：明细列表配分布图（图+文字展示）
                status_dist: dict[str, int] = {}
                for it in overdue_items:
                    label = it["状态"]
                    status_dist[label] = status_dist.get(label, 0) + 1
                result["items_dist"] = status_dist

            if "ticket.items" in keys:
                items = []
                for t in all_tickets:
                    created = t.created_at
                    updated = t.updated_at
                    is_new = created and start <= created <= end
                    has_update = updated and start <= updated <= end
                    if (is_new or has_update) and len(items) < 50:
                        items.append({
                            "工单ID": t.id,
                            "标题": t.title,
                            "描述": (t.description or "")[:200],
                            "状态": _cn_label(_TICKET_STATUS_CN, t.status, "未知"),
                            "类型": _cn_label(_TICKET_TYPE_CN, t.task_type, "其他"),
                            "优先级": _cn_label(_TICKET_PRIORITY_CN, t.priority, "中"),
                            "项目名称": t.project_name,
                            "创建时间": created.isoformat() if created else None,
                            "更新时间": updated.isoformat() if updated else None,
                            "解决时间": t.resolved_at.isoformat() if t.resolved_at else None,
                            # 周期内已解决的工单：给出从创建到解决的耗时，供报告展示处理时长
                            "解决耗时": _fmt_solve_duration(created, t.resolved_at)
                            if (t.resolved_at and start <= t.resolved_at <= end)
                            else None,
                        })
                result["items"] = items
                # 顺带按状态分布：明细列表配分布图（图+文字展示）
                status_dist: dict[str, int] = {}
                for it in items:
                    label = it["状态"]
                    status_dist[label] = status_dist.get(label, 0) + 1
                result["items_dist"] = status_dist

            return result
        finally:
            db.close()

    # ── 项目维度指标采集 ────────────────────────────────────

    def _collect_project_metrics(
        self, keys: set[str], start: datetime, end: datetime, explicit_time: bool = False
    ) -> dict:
        """一次性采集所有请求的项目指标。

        explicit_time=True（用户显式提及时间）时，项目自身维度指标
        （数量/状态/明细）按 settlement_period（业绩核算期）落在窗口
        覆盖月份内过滤；未显式提及查全部。

        project.no_data_items 例外：「搬运效率为空」问的是 collection_data
        的采集数据而非项目本身，时间口径来自采集窗口，判定对象为全部
        用户关联项目，不受 settlement_period 过滤影响。
        """
        db = self._get_db()
        try:
            q = db.query(ProjectDelivery)
            if self._project_ids:
                q = q.filter(ProjectDelivery.id.in_(self._project_ids))

            projects = q.all()
            result: dict = {}

            # 项目自身指标使用的集合（可被 settlement_period 过滤）
            scope_projects = projects
            if explicit_time:
                months = _settlement_month_keys(start, end)
                scope_projects = [
                    p for p in projects
                    if _norm_settlement_period(p.settlement_period) in months
                ]

            if "project.total" in keys:
                result["total"] = len(scope_projects)

            if "project.active_count" in keys:
                result["active_count"] = sum(
                    1 for p in scope_projects if _norm_enum(p.status) == "active"
                )

            if "project.completed_count" in keys:
                result["completed_count"] = sum(
                    1 for p in scope_projects
                    if _norm_enum(p.status) in ("completed", "done", "closed")
                )

            if "project.on_hold_count" in keys:
                result["on_hold_count"] = sum(
                    1 for p in scope_projects
                    if _norm_enum(p.status) in ("on_hold", "paused", "suspended")
                )

            if "project.by_status" in keys:
                dist: dict[str, int] = {}
                for p in scope_projects:
                    label = _cn_label(_PROJECT_STATUS_CN, p.status, "未知")
                    dist[label] = dist.get(label, 0) + 1
                result["by_status"] = dist

            if "project.no_data_items" in keys:
                # 窗口内无 collection_data 上报记录的项目清单：
                # 采集记录窗口与查询窗口有重叠（start_time_int <= end 且 end_time_int >= start）
                # 即视为「有数据」，其余项目为无数据。
                # 判定对象为全部用户关联项目（projects 全集），不受 settlement_period
                # 显式时间过滤影响：本项目指标问的是采集数据口径，不是项目口径。
                start_ts = int(start.timestamp())
                end_ts = int(end.timestamp())
                reported_rows = (
                    db.query(CollectionData.project)
                    .filter(
                        CollectionData.indicator == COLLECTION_EFFICIENCY_INDICATOR,
                        CollectionData.start_time_int <= end_ts,
                        CollectionData.end_time_int >= start_ts,
                    )
                    .all()
                )
                reported = {row[0] for row in reported_rows if row[0]}
                no_data_items = [
                    {
                        "项目ID": p.id,
                        "项目代码": p.code,
                        "项目名称": p.name,
                        "状态": _cn_label(_PROJECT_STATUS_CN, p.status, "未知"),
                    }
                    for p in projects
                    if str(p.id) not in reported
                ]
                result["no_data_list"] = no_data_items
                result["no_data_count"] = len(no_data_items)
                result["with_data_count"] = len(projects) - len(no_data_items)

            need_items = "project.items" in keys
            if need_items:
                members_by_project = self._get_project_members_map(
                    db, [p.id for p in scope_projects if p.id]
                )
                # 按状态分组输出，组内截断：
                # - 「状态」提为组标签，组内条目不再重复状态字段；
                # - 每组最多 _PROJECT_ITEMS_PER_GROUP_LIMIT 条，超出只计数量；
                # - 组按项目数降序，先呈现大头状态。
                groups: dict[str, list[dict]] = {}
                for p in scope_projects:
                    label = _cn_label(_PROJECT_STATUS_CN, p.status, "未知")
                    groups.setdefault(label, []).append({
                        "项目ID": p.id,
                        "项目代码": p.code,
                        "项目名称": p.name,
                        "问题数": p.issues,
                        "风险数": p.risks,
                        "对接人": p.contact_person,
                        "成员": members_by_project.get(p.id, []),
                    })
                result["items_by_status"] = [
                    {
                        "状态": label,
                        "项目数": len(group),
                        "项目": group[: _PROJECT_ITEMS_PER_GROUP_LIMIT],
                        "已截断": len(group) > _PROJECT_ITEMS_PER_GROUP_LIMIT,
                    }
                    for label, group in sorted(
                        groups.items(), key=lambda kv: len(kv[1]), reverse=True
                    )
                ]
                result["items_count"] = len(scope_projects)
                # 顺带按状态分布：明细列表配分布图（图+文字展示）
                result["items_dist"] = {
                    g["状态"]: g["项目数"] for g in result["items_by_status"]
                }

            return result
        finally:
            db.close()

    # ── 风险维度指标采集（规则推算口径） ────────────────────

    def _collect_risk_metrics(
        self, keys: set[str], start: datetime, end: datetime
    ) -> dict:
        """一次性采集所有请求的风险指标（推算口径）。

        数据来源不再是 risk 表，而是：
        - project 表（评估对象清单，受 _project_ids 范围约束）
        - project_info_node / project_info_value（AGV 数量/种类、项目类型、人工风险点）
        - tasks 表（未关闭工单数、近30天新增工单数，报障/bug 类加权）
        由 risk_assessor 按 yaml 规则推算分数与等级，结果确定性可复现。
        """
        db = self._get_db()
        try:
            project_q = db.query(ProjectDelivery)
            if self._project_ids:
                project_q = project_q.filter(ProjectDelivery.id.in_(self._project_ids))
            projects = project_q.all()

            assessments = assess_projects(self._build_risk_signals(db, projects))
            # 高风险优先排序：分数降序，明细列表与 LLM 一眼看到大头
            assessments.sort(key=lambda x: -x["风险分数"])

            by_level: dict[str, int] = {}
            for item in assessments:
                level = item["风险等级"]
                by_level[level] = by_level.get(level, 0) + 1

            return {
                "assessment": assessments[:_RISK_ITEMS_LIMIT],  # 与指标 key risk.assessment 对齐（build_charts 按后缀取值）
                "items_dist": by_level,   # 明细列表配等级分布图
                "by_level": by_level,
                "high_risk_count": by_level.get("高", 0),
                "assessed_count": len(assessments),
            }
        finally:
            db.close()

    def _build_risk_signals(self, db, projects) -> list[ProjectRiskSignals]:
        """汇总项目信息节点值与工单数据，构建风险推算信号。

        节点取值口径（与 backend info_node_service 一致）：
        - select 节点（项目类型/风险点/车型）：value_json 为 {"selected": ...}
          或原生字符串；车型节点仅作「数量 > 0」的种类判定
        - text 节点（总车数/车型数量）：原生数字字符串
        """
        if not projects:
            return []
        project_ids = [p.id for p in projects if p.id]

        # 1. 项目信息节点定义与值
        node_rows = (
            db.query(ProjectInfoNode.id, ProjectInfoNode.node_key)
            .filter(
                ProjectInfoNode.node_key.in_(set(_RISK_NODE_KEYS.values())),
                ProjectInfoNode.project_id.is_(None),
            )
            .all()
        )
        id_by_key = {node_key: node_id for node_id, node_key in node_rows}
        values: dict = {}
        if id_by_key:
            for v in (
                db.query(ProjectInfoValue)
                .filter(
                    ProjectInfoValue.project_id.in_(project_ids),
                    ProjectInfoValue.node_id.in_(set(id_by_key.values())),
                )
                .all()
            ):
                values[(str(v.project_id), v.node_id)] = v.value_json

        # 2. 工单聚合：未关闭 + 近30天新增（报障/bug 加权）
        open_map: dict[str, int] = {}
        open_problem_map: dict[str, int] = {}
        new_map: dict[str, int] = {}
        new_problem_map: dict[str, int] = {}
        window_start = datetime.now() - timedelta(days=_RISK_NEW_TICKET_WINDOW_DAYS)
        for row in (
            db.query(Task.project_id, Task.status, Task.task_type, Task.created_at)
            .filter(Task.project_id.in_(project_ids))
            .all()
        ):
            project_id, status, task_type, created_at = row
            if not project_id:
                continue
            pid = str(project_id)
            is_open = _norm_enum(status) in ("new", "in_progress", "pending")
            is_problem = _norm_enum(task_type) in ("problem", "bug")
            if is_open:
                open_map[pid] = open_map.get(pid, 0) + 1
                if is_problem:
                    open_problem_map[pid] = open_problem_map.get(pid, 0) + 1
            if created_at and created_at >= window_start:
                new_map[pid] = new_map.get(pid, 0) + 1
                if is_problem:
                    new_problem_map[pid] = new_problem_map.get(pid, 0) + 1

        # 3. 组装信号
        signals: list[ProjectRiskSignals] = []
        for p in projects:
            pid = str(p.id)
            signals.append(ProjectRiskSignals(
                project_id=pid,
                project_name=p.name or pid,
                open_tickets=open_map.get(pid, 0),
                open_problem_tickets=open_problem_map.get(pid, 0),
                new_30d_tickets=new_map.get(pid, 0),
                new_30d_problem_tickets=new_problem_map.get(pid, 0),
                agv_count=_agv_count_of(values, pid, id_by_key),
                agv_model_count=_agv_model_count_of(values, pid, id_by_key),
                project_type=_risk_select_value(values, pid, id_by_key, "project_type"),
                manual_risk=_risk_select_value(values, pid, id_by_key, "manual_risk"),
            ))
        return signals

    # ── 采集数据（collection_data）维度指标采集 ──────────────

    def _collect_collection_metrics(
        self, keys: set[str], start: datetime, end: datetime
    ) -> dict:
        """采集 collection_data 表的搬运效率指标（字段口径与搬运效率分析页面一致）。

        查询口径：
        - indicator == GroupEfficiency（数据导入落库的搬运效率标签）；
        - start_time_int / end_time_int 为秒级时间戳，查询窗口前后各放宽 1 天
          （与搬运效率分析服务的宽窗口策略一致），再用记录 data JSON 自带的
          start_time 日期精确归入目标窗口；
        - 标量指标与各组对比取窗口内最新一条记录（搬运效率分析页面为单日口径），
          collection.items 返回窗口内每条记录（每日/每项目）的汇总明细。
        """
        db = self._get_db()
        try:
            start_ts = int((start - timedelta(days=1)).timestamp())
            end_ts = int((end + timedelta(days=1)).timestamp())

            q = db.query(CollectionData).filter(
                CollectionData.indicator == COLLECTION_EFFICIENCY_INDICATOR,
                CollectionData.start_time_int >= start_ts,
                CollectionData.end_time_int <= end_ts,
            )
            if self._project_ids:
                q = q.filter(CollectionData.project.in_(self._project_ids))
            rows = q.order_by(CollectionData.start_time_int.desc()).all()

            result: dict = {}
            if not rows:
                return result

            # 解析每条记录并按记录自带 start_time 的日期精确过滤目标窗口
            day_start = start.strftime("%Y-%m-%d")
            day_end = end.strftime("%Y-%m-%d")
            entries: list[dict] = []
            for row in rows:
                try:
                    data_obj = json.loads(row.data)
                except (json.JSONDecodeError, TypeError):
                    continue
                metrics = _find_group_efficiency_metrics(data_obj)
                if not metrics:
                    continue
                start_iso = (
                    data_obj.get("start_time")
                    if isinstance(data_obj, dict)
                    else None
                )
                day = None
                if isinstance(start_iso, str) and len(start_iso) >= 10:
                    day = start_iso[:10]
                    if not (day_start <= day <= day_end):
                        continue
                else:
                    # 老数据无 start_time：退回 start_time_int 推导日期
                    try:
                        day = datetime.fromtimestamp(
                            int(row.start_time_int)
                        ).strftime("%Y-%m-%d")
                    except (TypeError, ValueError, OSError):
                        day = day_start
                summary, robots = _compute_collection_summary_and_robots(metrics)
                entries.append({
                    "project": row.project,
                    "day": day,
                    "summary": summary,
                    "robots": robots,
                })

            if not entries:
                return result

            # 窗口内最新一条记录：标量卡片与各组数据对比
            latest = entries[0]

            # 窗口内按天聚合（每天取最新一条，entries 已按时间倒序，
            # 每个 day 首次出现即该天最新记录）：多日趋势图数据源
            by_day: dict[str, dict] = {}
            for entry in entries:
                if entry["day"] not in by_day:
                    by_day[entry["day"]] = entry["summary"]
            if len(by_day) >= 2:
                result["by_day"] = by_day

            if "collection.items" in keys:
                items = []
                for entry in entries:
                    items.append({
                        "项目": entry["project"],
                        "日期": entry["day"],
                        **entry["summary"],
                    })
                result["items"] = items

            if "collection.robot_group_compare" in keys:
                result["robot_group_compare"] = latest["robots"]

            # 标量指标：全部取最新记录的汇总字段
            scalar_fields = {
                "collection.total_tasks": "total_tasks",
                "collection.carry_task_count": "carry_task_count",
                "collection.effective_work_hours": "effective_work_hours",
                "collection.fault_hours": "fault_hours",
                "collection.idle_hours": "idle_hours",
                "collection.avg_error_count": "avg_error_count",
                "collection.avg_fault_duration_minutes": "avg_fault_duration_minutes",
                "collection.avg_carry_duration_minutes": "avg_carry_duration_minutes",
                "collection.avg_manual_switch_count": "avg_manual_switch_count",
                "collection.manual_intervention_rate": "manual_intervention_rate",
            }
            summary = latest["summary"]
            for key, field in scalar_fields.items():
                if key in keys:
                    result[field] = summary.get(field)

            return result
        finally:
            db.close()

    # ── 项目信息维度指标采集 ────────────────────────────────

    def _collect_project_info_metrics(
        self, keys: set[str], start: datetime, end: datetime
    ) -> dict:
        """采集项目信息管理四张表的指标（表结构与 backend delivery.py 对齐）。

        数据关系：
        - project_info_node：字段定义（project_id IS NULL=全局模板，非空=项目增补），
          只描述结构不存值；
        - project_info_value：各项目实际值，UNIQUE(project_id, node_id)，不预建空行；
        - project_info_value_history：值/结构变更历史，changed_at 为时间口径，
          old/new_value 为原生 JSON 前后值；
        - project_info_node_mark：节点关注（主键 node_id+operator，每人一份）。

        范围过滤：传入 project_ids 时，节点取「全局模板 + 范围内项目增补」，
        值/历史/关注一律按 project_id 过滤；未传入则全量统计。
        填写率分母：全局 field 节点数 × 项目数 + 各项目增补 field 节点数。
        """
        db = self._get_db()
        try:
            # 节点：active 的全局模板节点 +（范围过滤下）项目增补节点
            node_q = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.status == "active"
            )
            if self._project_ids:
                node_q = node_q.filter(
                    sa_or(
                        ProjectInfoNode.project_id.is_(None),
                        ProjectInfoNode.project_id.in_(self._project_ids),
                    )
                )
            nodes = node_q.all()
            field_nodes = [n for n in nodes if n.node_type == "field"]
            global_fields = [n for n in field_nodes if n.project_id is None]
            node_map = {n.id: n for n in nodes}

            result: dict = {}

            # -- 节点统计 --
            if "project_info.node_total" in keys:
                result["node_total"] = len(nodes)
            if "project_info.global_node_count" in keys:
                result["global_node_count"] = sum(
                    1 for n in nodes if n.project_id is None
                )
            if "project_info.custom_node_count" in keys:
                result["custom_node_count"] = sum(
                    1 for n in nodes if n.project_id is not None
                )

            # -- 字段值类型分布（只统计 field 节点，root/group 无值语义） --
            if "project_info.by_value_type" in keys:
                by_type: dict[str, int] = {}
                for n in field_nodes:
                    vt = n.value_type or "text"
                    by_type[vt] = by_type.get(vt, 0) + 1
                result["by_value_type"] = by_type

            # -- 项目口径：范围过滤下的项目清单 --
            project_q = db.query(ProjectDelivery.id, ProjectDelivery.name)
            if self._project_ids:
                project_q = project_q.filter(
                    ProjectDelivery.id.in_(self._project_ids)
                )
            project_rows = project_q.all()
            project_names = {
                str(pid): name for pid, name in project_rows if pid
            }
            project_ids = list(project_names.keys())

            # -- 已填值（不预建空行，有行即已填） --
            value_q = db.query(ProjectInfoValue)
            if self._project_ids:
                value_q = value_q.filter(
                    ProjectInfoValue.project_id.in_(project_ids)
                )
            values = value_q.all()
            filled_by_project: dict[str, int] = {}
            for v in values:
                filled_by_project[v.project_id] = (
                    filled_by_project.get(v.project_id, 0) + 1
                )

            # -- 变更历史 --
            hist_q = db.query(ProjectInfoValueHistory)
            if self._project_ids:
                hist_q = hist_q.filter(
                    ProjectInfoValueHistory.project_id.in_(project_ids)
                )
            histories = hist_q.all()

            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            if "project_info.change_count" in keys or "project_info.change_by_day" in keys:
                by_day: dict[str, int] = {}
                in_range: list = []
                for h in histories:
                    day = (h.changed_at or "")[:10]
                    if day and start_str <= day <= end_str:
                        in_range.append(h)
                        by_day[day] = by_day.get(day, 0) + 1
                if "project_info.change_count" in keys:
                    result["change_count"] = len(in_range)
                    # 顺带按天序列：标量指标配趋势图（图+文字展示）
                    result["change_count_by_day"] = dict(sorted(by_day.items()))
                if "project_info.change_by_day" in keys:
                    result["change_by_day"] = dict(sorted(by_day.items()))

            if "project_info.change_by_type" in keys:
                by_op: dict[str, int] = {}
                for h in histories:
                    op = _PROJECT_INFO_OP_CN.get(
                        h.operation_type or "", h.operation_type or "未知"
                    )
                    by_op[op] = by_op.get(op, 0) + 1
                result["change_by_type"] = by_op

            # -- 填写率与完整度明细（共用一次聚合） --
            if any(
                k in keys
                for k in ("project_info.fill_rate", "project_info.items")
            ):
                custom_fields_by_project: dict[str, int] = {}
                for n in field_nodes:
                    if n.project_id:
                        custom_fields_by_project[n.project_id] = (
                            custom_fields_by_project.get(n.project_id, 0) + 1
                        )
                fillable_by_project = {
                    pid: len(global_fields) + custom_fields_by_project.get(pid, 0)
                    for pid in project_ids
                }
                fillable_total = sum(fillable_by_project.values())
                filled_total = sum(filled_by_project.values())

                if "project_info.fill_rate" in keys:
                    rate = (
                        (filled_total / fillable_total * 100)
                        if fillable_total
                        else 0.0
                    )
                    result["fill_rate"] = round(rate, 1)
                    result["filled_node_count"] = filled_total
                    result["fillable_node_count"] = fillable_total

                if "project_info.items" in keys:
                    last_change: dict[str, str] = {}
                    for h in histories:
                        if not h.project_id:
                            continue
                        cur = last_change.get(h.project_id) or ""
                        if (h.changed_at or "") > cur:
                            last_change[h.project_id] = h.changed_at
                    rows = []
                    for pid in project_ids:
                        filled = filled_by_project.get(pid, 0)
                        fillable = fillable_by_project.get(pid, 0)
                        rows.append({
                            "项目名称": project_names.get(pid, pid),
                            "已填字段数": filled,
                            "可填字段数": fillable,
                            "填写率": round(filled / fillable * 100, 1) if fillable else 0.0,
                            "最近变更时间": last_change.get(pid),
                        })
                    rows.sort(key=lambda r: (-r["填写率"], -r["已填字段数"]))
                    result["items"] = rows[:50]

            # -- 已填字段值明细（可按值内容回答具体字段问题） --
            if "project_info.value_items" in keys:
                ordered = sorted(
                    values,
                    key=lambda v: (v.project_id or "", v.node_id or ""),
                )
                items = []
                for v in ordered:
                    node = node_map.get(v.node_id)
                    raw = v.value_json
                    if isinstance(raw, (dict, list)):
                        raw = json.dumps(raw, ensure_ascii=False)
                    items.append({
                        "项目名称": project_names.get(v.project_id, v.project_id),
                        "字段名": node.node_name if node else v.node_id,
                        "值": (str(raw) if raw is not None else "")[:120],
                        "更新时间": v.updated_at,
                    })
                    if len(items) >= 100:
                        break
                result["value_items"] = items

            # -- 被关注最多的节点（星标，project_info_node_mark） --
            if "project_info.top_marked_nodes" in keys:
                mark_q = db.query(ProjectInfoNodeMark)
                if self._project_ids:
                    mark_q = mark_q.filter(
                        ProjectInfoNodeMark.project_id.in_(project_ids)
                    )
                marks = mark_q.all()
                mark_count: dict[str, int] = {}
                for m in marks:
                    mark_count[m.node_id] = mark_count.get(m.node_id, 0) + 1
                top = sorted(
                    mark_count.items(), key=lambda kv: kv[1], reverse=True
                )[:10]
                result["top_marked_nodes"] = [
                    {
                        "节点名": (
                            node_map[nid].node_name if nid in node_map else nid
                        ),
                        "关注数": cnt,
                    }
                    for nid, cnt in top
                ]

            return result
        finally:
            db.close()

    # ── 汇总采集 ──────────────────────────────────────────────

    def collect_all(
        self, start: datetime, end: datetime, date_range_str: str
    ) -> CollectedData:
        """汇总采集所有维度数据。"""
        logger.info("开始采集报告数据 range=%s projects=%s", date_range_str, self._project_ids)

        project = self.collect_project_data()
        risk = self.collect_risk_data(start, end)
        ticket = self.collect_ticket_data(start, end)

        data = CollectedData(
            date_range=date_range_str,
            project=project,
            risk=risk,
            ticket=ticket,
        )
        logger.info(
            "数据采集完成 projects=%d risk_score=%d tickets=%d",
            project.total, risk.score, ticket.total,
        )
        return data


# ── 报告生成器 ─────────────────────────────────────────────────────

# 报告输出预算：模板化报告为长文本，且已显式关闭思考（thinking=False），
# 预算全部留给正文；过小会导致正文被截断甚至为空（前端表现为空白）。
REPORT_MAX_TOKENS = 12000


class ReportGenerator:
    """日报/周报生成器。

    编排数据采集 → 构建 prompt → 调用 LLM → 解析结构化结果。
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    @staticmethod
    def _resolve_project_ids_by_user(user_id: str) -> list[str]:
        """通过 users ↔ user_project_roles 关联，获取该用户关联的全部 project_id。

        关联逻辑：user_project_roles.user_id ↔ users.id；返回的 project_id
        即 project.id（与 project.code 一致），可直接用于
        ProjectDelivery.id / Task.project_id 过滤。

        兼容两种传参：users.id 或 username（前端登录态只有 username，
        按 id 关联不到时回退按 username 关联再查）。
        """
        db = SessionLocal()
        try:
            rows = (
                db.query(UserProjectRole)
                .join(User, User.id == UserProjectRole.user_id)
                .filter(User.id == user_id)
                .all()
            )
            if not rows:
                rows = (
                    db.query(UserProjectRole)
                    .join(User, User.id == UserProjectRole.user_id)
                    .filter(User.username == user_id)
                    .all()
                )
            project_ids = [r.project_id for r in rows if r.project_id]
            logger.info("user_id=%s 关联项目 %d 个: %s", user_id, len(project_ids), project_ids)
            return project_ids
        finally:
            db.close()

    @staticmethod
    def lookup_project_by_hint(hint: str, user_id: str | None = None) -> str | None:
        """从问题文本提取的项目名线索中匹配 project 表，返回 project.code。

        匹配优先级（字面模糊 + 消歧决策）：code 精确 → name 精确 →
        name 包含 → 片段覆盖 + bigram 相似。有 user_id 时优先在用户关联
        项目（user_project_roles）范围内匹配，未命中或无 user_id 时回退
        全局匹配。多候选并列/低置信时不猜（返回 None），由 clarify
        候选按钮消歧。

        Args:
            hint: 从问题中提取的项目名线索（如 "XX"、"XX项目" 中的 XX）。
                小于 2 字符直接返回 None。
            user_id: 登录用户 ID，限定优先匹配范围（可为 None）。
        """
        if not hint or len(hint) < 2:
            return None
        from .project_matcher import resolve_project

        scope_ids: list[str] | None = None
        if user_id:
            try:
                scope_ids = ReportGenerator._resolve_project_ids_by_user(user_id) or None
            except Exception as exc:
                logger.warning("用户关联项目查询失败: %s", exc)
        if scope_ids:
            code, _ = resolve_project(hint, scope_ids)
            if code:
                return code
        code, _ = resolve_project(hint)
        return code

    def collect_data(
        self,
        period: ReportPeriod,
        target_date: date,
        project_code: str,
    ) -> CollectedData:
        """按报告口径从 MySQL 采集分析数据（仅限指定项目）。"""
        start, end, date_range_str = _resolve_period_range(period, target_date)

        collector = ReportDataCollector(project_ids=[project_code])
        return collector.collect_all(start, end, date_range_str)

    async def generate(
        self,
        period: ReportPeriod,
        target_date: date,
        project_code: str,
    ) -> ReportResult:
        """生成指定项目的日报或周报（项目必选，统计范围仅限该项目）。"""
        # 1. 计算时间范围
        _, _, date_range_str = _resolve_period_range(period, target_date)

        # 2. 采集数据（仅指定项目）
        collected = self.collect_data(
            period=period,
            target_date=target_date,
            project_code=project_code,
        )

        # 3. 序列化为 JSON 文本
        data_text = json.dumps(collected.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str)

        # 4. 构建 prompt（单项目模板）
        system_prompt = build_report_system_prompt(period)
        user_prompt = build_report_user_prompt(
            data_text=data_text,
            date_range=date_range_str,
            period=period,
            project_code=project_code,
        )

        # 5. 调用 LLM（报告为模板化写作：关闭思考模式，避免 reasoning 占满
        #    max_tokens 预算导致正文为空/被截断，输出预算见 REPORT_MAX_TOKENS）
        logger.info("开始生成%s date_range=%s project=%s", period.value, date_range_str, project_code)
        raw_response, usage = await self._llm.chat(
            system_prompt,
            user_prompt,
            max_tokens=REPORT_MAX_TOKENS,
            thinking=False,
        )

        # 6. 解析结果
        sections = self._parse_sections(raw_response, collected)
        summary = self._extract_summary(raw_response)

        return ReportResult(
            period=period,
            date_range=date_range_str,
            sections=sections,
            summary=summary,
            raw_response=raw_response,
            generated_at=datetime.now().isoformat(),
            project_code=project_code,
        )

    async def generate_stream(
        self,
        period: ReportPeriod,
        target_date: date,
        project_code: str,
    ) -> AsyncIterator[str]:
        """流式生成指定项目的报告，逐 chunk 返回文本。"""
        # 1. 计算时间范围
        _, _, date_range_str = _resolve_period_range(period, target_date)

        # 2. 采集数据（仅指定项目）
        collected = self.collect_data(
            period=period,
            target_date=target_date,
            project_code=project_code,
        )

        # 3. 序列化
        data_text = json.dumps(collected.model_dump(by_alias=True), ensure_ascii=False, indent=2, default=str)

        # 4. 构建 prompt（单项目模板）
        system_prompt = build_report_system_prompt(period)
        user_prompt = build_report_user_prompt(
            data_text=data_text,
            date_range=date_range_str,
            period=period,
            project_code=project_code,
        )

        # 5. 流式调用 LLM（关闭思考模式：reasoning_content 不计入正文却占满
        #    输出预算，曾导致正文为空、前端空白；预算见 REPORT_MAX_TOKENS）
        async for chunk in self._llm.chat_stream(
            system_prompt,
            user_prompt,
            max_tokens=REPORT_MAX_TOKENS,
            thinking=False,
        ):
            yield chunk

    # ── 结果解析 ──────────────────────────────────────────────

    @staticmethod
    def _parse_sections(raw: str, collected: CollectedData) -> list[ReportSection]:
        """从 LLM 回复中提取各章节。"""
        sections: list[ReportSection] = []

        # 匹配 ## 标题及内容
        pattern = r"^##\s+(.+?)\n((?:(?!^##\s).+?\n)*)"
        for match in re.finditer(pattern, raw, re.MULTILINE):
            title = match.group(1).strip()
            content = match.group(2).strip()
            if not content:
                continue

            # 附加指标数据
            metrics: dict = {}
            title_lower = title.lower()
            if "项目" in title_lower:
                metrics = {
                    "total": collected.project.total,
                    "active": collected.project.active,
                    "completed": collected.project.completed,
                    "on_hold": collected.project.on_hold,
                }
            elif "风险" in title_lower:
                metrics = {
                    "score": collected.risk.score,
                    "level": collected.risk.level,
                    "open_tickets": collected.risk.open_tickets,
                    "new_30d_tickets": collected.risk.new_30d_tickets,
                    "factors": collected.risk.factors,
                }
            elif "工单" in title_lower:
                metrics = {
                    "total": collected.ticket.total,
                    "new": collected.ticket.new_tickets,
                    "resolved": collected.ticket.resolved,
                    "closed": collected.ticket.closed,
                    "overdue": collected.ticket.overdue,
                    "resolve_rate": collected.ticket.resolve_rate,
                    "by_status": collected.ticket.by_status,
                    "by_type": collected.ticket.by_type,
                }

            sections.append(ReportSection(title=title, content=content, metrics=metrics))

        return sections

    @staticmethod
    def _extract_summary(raw: str) -> str:
        """提取摘要段落。"""
        # 尝试匹配 **摘要** 或 ## 摘要
        patterns = [
            r"\*\*摘要\*\*[：:\s]*(.+?)(?:\n\n|$)",
            r"##\s*摘要[：:\s]*(.+?)(?:\n\n|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                return match.group(1).strip()

        # 兜底：取最后一段非空文本
        lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
        if lines:
            return lines[-1]
        return ""


# ── 顶层入口（供定时任务调用）─────────────────────────────────────

async def generate_report(
    period: str = "daily",
    date: str | None = None,
    project_code: str | None = None,
) -> ReportResult:
    """顶层报告生成入口。

    设计为无 HTTP 依赖的纯 async 函数，可直接被
    APScheduler / Celery Beat / cron 脚本调用。

    Args:
        period: "daily" 或 "weekly"
        date: 目标日期 YYYY-MM-DD，默认今天
        project_code: 项目代码（必填，报告仅统计该项目）

    Returns:
        ReportResult 结构化报告结果

    Raises:
        ValueError: project_code 为空
    """
    if not project_code:
        raise ValueError("project_code 为必填参数：日报/周报仅统计指定单个项目")

    from .config import AnalysisConfig

    report_period = ReportPeriod(period)
    target = _parse_date(date)

    config = AnalysisConfig.from_env()
    llm = LLMClient(config)
    generator = ReportGenerator(llm)

    return await generator.generate(
        period=report_period,
        target_date=target,
        project_code=project_code,
    )


async def generate_report_stream(
    period: str = "daily",
    date: str | None = None,
    project_code: str | None = None,
) -> AsyncIterator[str]:
    """顶层流式报告生成入口（项目必选）。"""
    if not project_code:
        raise ValueError("project_code 为必填参数：日报/周报仅统计指定单个项目")

    from .config import AnalysisConfig

    report_period = ReportPeriod(period)
    target = _parse_date(date)

    config = AnalysisConfig.from_env()
    llm = LLMClient(config)
    generator = ReportGenerator(llm)

    async for chunk in generator.generate_stream(
        period=report_period,
        target_date=target,
        project_code=project_code,
    ):
        yield chunk
