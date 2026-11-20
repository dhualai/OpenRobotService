"""项目工单卡服务（项目详情页「项目工单」）—— 工单概览 + 核心阻滞工单 + AI 阻滞权重配置。

数据源：系统任务模块 tasks 表（app.models.task.Task，Task.project_id = 项目ID/代码）。
卡片三部分的取数口径：
1. 顶部汇总（总工单数 / 正在处理 / 已完成）：直接复用 task_dashboard_service.get_ticket_summary
   的「单项目」口径（monitored 六状态之和为总数，与仪表盘统计卡一致；前端按
   正在处理 = 处理中 + 挂起、已完成 = 已解决 + 已关闭 派生三格数字，接口返回完整 by_status）；
2. 工单变化趋势：按自然周（周一为一周起点）统计该项目每周新建工单数，近 TREND_WEEKS 周；
3. 核心阻滞工单：
   - 已配置：读取 project_blocking_config 里 AI 选出的工单ID 列表（按下单顺序展示，
     工单已不存在的静默剔除；列表全空时退回默认规则）；
   - 未配置：默认规则 = 未完成工单（new/处理中/挂起）按「优先级 > 超期最久 > 最新创建」排序取前三。

「配置阻滞权重」仅管理员及超级管理员（接口层 get_current_admin_user 把关）：把项目基础
字段 + 该项目工单基础数据 + 管理员提示词交给大模型，模型输出最重要阻滞工单的 JSON
（ticket_ids + summary + reasons），落 project_blocking_config 表；大模型接线与
「AI 项目摘要」共用同一客户端（app/core/llm_client.py 的 LLMClient，backend 自维护）。

异常约定（接口层映射）：ValueError → 400；LookupError → 404；RuntimeError → 503（AI 未配置/调用失败）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.llm_client import get_llm_client
from app.models.delivery import ProjectBlockingConfig
from app.models.task import Task, TaskStatus
from app.modules.admin.services.task_dashboard_service import task_dashboard_service
from app.services.user_service import user_service

logger = logging.getLogger("admin")

# 趋势窗口：近 8 个自然周（含本周），周一为每周起点
TREND_WEEKS = 8
# 未配置时的默认阻滞候选条数 / AI 候选池条数 / AI 最多选出的阻滞工单数
DEFAULT_BLOCKING_LIMIT = 3
AI_CANDIDATE_LIMIT = 60
AI_BLOCKING_LIMIT = 5
# 阻滞工单条目里「问题概况」摘要长度（字符）
DESCRIPTION_PREVIEW_CHARS = 80
# 送大模型的单条工单描述截断长度
AI_DESCRIPTION_CHARS = 200

# 默认阻滞规则视为「未完成」的状态（new 还没派单，但对项目仍是未决问题，参与筛选）
OPEN_FOR_BLOCKING = [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.PENDING]
# 优先级排序权重：数字越小越优先
PRIORITY_WEIGHT = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
# 送大模型时的状态中文名（与前端展示口径一致）
STATUS_LABELS = {
    "new": "新建",
    "in_progress": "处理中",
    "pending": "挂起",
    "resolved": "已解决",
    "closed": "已关闭",
    "canceled": "已取消",
}
PRIORITY_LABELS = {"urgent": "紧急", "high": "高", "medium": "中", "low": "低"}
TYPE_LABELS = {"problem": "报障", "bug": "缺陷", "feature": "功能需求", "support": "支持请求", "other": "其他"}

# 阻滞判定送大模型的项目基础字段（项目 dict 键 → 中文标签）
BLOCKING_PROJECT_FIELDS = [
    ("name", "项目名称"),
    ("project_code", "项目编号"),
    ("status", "项目阶段"),
    ("project_type", "项目类型"),
    ("project_region", "项目区域/地点"),
    ("description", "项目描述"),
    ("project_manager", "项目经理"),
    ("special_attention", "特别关注"),
    ("risk_task_description", "风险和任务描述"),
]

_BLOCKING_SYSTEM_PROMPT = (
    "你是一名企业项目交付管理专家，负责从项目工单清单中判定「当前最阻碍项目推进的核心问题工单」。"
    "只输出 JSON 对象，不要 Markdown 代码块、不要任何解释或寒暄。"
)

_PROMPT_TEMPLATE = """请根据以下项目资料与工单清单，判定该项目当前最重要的「核心阻滞工单」。

【管理员的判定要求（权重说明，优先级高于你的通用经验）】
{prompt}

【项目基础信息】
{fields}

【工单清单（共 {ticket_count} 条，按创建时间倒序）】
{tickets}

【输出格式（严格 JSON，不要代码块围栏）】
{{"ticket_ids": [工单ID按重要性从高到低排列，最多 {limit} 个],
  "summary": "一句话总述当前阻滞情况（60字以内）",
  "reasons": {{"工单ID": "该工单为何阻滞的原因（40字以内）"}}}}
要求：
1. ticket_ids 只允许出现在上方清单中的工单ID（整数，不带 #）；
2. 综合工单状态、优先级、是否超期、问题类型与描述判断：未完成、超期、高优先级、涉及面广的工单更可能阻滞项目；
3. 已解决/已关闭/已取消的工单原则上不选（除非清单中没有未完成工单）；
4. reasons 的键必须与 ticket_ids 中的工单ID一一对应。"""


# ── 纯函数（供单测）─────────────────────────────────────

def week_start_of(moment: datetime) -> datetime:
    """返回 moment 所在自然周的周一 00:00（截断时分秒）。"""
    return datetime(moment.year, moment.month, moment.day) - timedelta(days=moment.weekday())


def build_weekly_trend(created_ats: List[datetime], now: datetime, weeks: int = TREND_WEEKS) -> List[Dict[str, Any]]:
    """按周分桶统计工单创建数：返回近 weeks 周（含本周）[{week_start: 'YYYY-MM-DD', count}]。

    窗口外的记录忽略；没有记录的周计 0（保证柱状图横轴完整）。
    """
    current = week_start_of(now)
    starts = [current - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]
    counts = {start: 0 for start in starts}
    for ts in created_ats:
        if ts is None:
            continue
        ws = week_start_of(ts)
        if ws in counts:
            counts[ws] += 1
    return [{"week_start": start.strftime("%Y-%m-%d"), "count": counts[start]} for start in starts]


def default_blocking_order(tasks: List[Task]) -> List[Task]:
    """默认阻滞排序：优先级（紧急→低）> 截止时间早者在前（无截止置后）> 创建时间新者在前。

    两次稳定排序完成（先按创建时间倒序，再按优先级+截止时间）：
    避免在 Windows 上对 datetime.min 取 timestamp() 报错。
    """
    ordered = sorted(tasks, key=lambda task: task.created_at or datetime.min, reverse=True)
    ordered.sort(key=lambda task: (
        PRIORITY_WEIGHT.get(task.priority.value if task.priority else "", 9),
        task.deadline_at or datetime.max,
    ))
    return ordered


def build_blocking_prompt(project: Dict[str, Any], tickets: List[Dict[str, Any]], user_prompt: str,
                          limit: int = AI_BLOCKING_LIMIT) -> str:
    """组装阻滞判定提示词（纯函数，供单测）。tickets 为 _ticket_for_ai 的输出列表。"""
    field_lines = []
    for key, label in BLOCKING_PROJECT_FIELDS:
        value = project.get(key)
        if value is None or value == "" or value == []:
            continue
        field_lines.append(f"{label}：{value}")
    fields = "\n".join(field_lines) or "（未填写）"

    ticket_lines = []
    for item in tickets:
        parts = [
            f"#{item['id']}",
            f"[状态={item.get('status_label') or item.get('status')}]",
            f"[优先级={item.get('priority_label') or item.get('priority')}]",
            f"[类型={item.get('type_label') or item.get('ticket_type')}]",
            f"标题：{item.get('title')}",
        ]
        if item.get("created_at"):
            parts.append(f"创建={item['created_at']}")
        if item.get("deadline_at"):
            parts.append(f"截止={item['deadline_at']}{'（已超期）' if item.get('overdue') else ''}")
        if item.get("creator_name"):
            parts.append(f"提单人={item['creator_name']}")
        if item.get("assignee_name"):
            parts.append(f"接单人={item['assignee_name']}")
        line = " ".join(parts)
        if item.get("description"):
            line += f"\n    描述：{item['description']}"
        ticket_lines.append(line)
    tickets_text = "\n".join(ticket_lines) or "（暂无工单）"

    return _PROMPT_TEMPLATE.format(
        prompt=user_prompt.strip(),
        fields=fields,
        ticket_count=len(tickets),
        tickets=tickets_text,
        limit=limit,
    )


def parse_blocking_result(text: str, valid_ids: set, limit: int = AI_BLOCKING_LIMIT) -> Dict[str, Any]:
    """解析大模型返回的阻滞判定 JSON。

    容错：剥离可能的 Markdown 围栏与前后杂文（取首个 { 到末个 } 之间）；工单ID 过滤到
    候选清单内并保序去重；解析失败 / 未选出任何有效工单时抛 RuntimeError（接口层 → 503）。
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("大模型返回内容无法解析为判定结果，请稍后重试或调整提示词")
    try:
        parsed = json.loads(raw[start:end + 1])
    except ValueError as exc:
        raise RuntimeError(f"大模型返回内容无法解析为判定结果（{exc}），请稍后重试或调整提示词")
    if not isinstance(parsed, dict):
        raise RuntimeError("大模型返回内容无法解析为判定结果，请稍后重试或调整提示词")

    ticket_ids: List[int] = []
    for value in parsed.get("ticket_ids") or []:
        try:
            ticket_id = int(value)
        except (TypeError, ValueError):
            continue
        if ticket_id in valid_ids and ticket_id not in ticket_ids:
            ticket_ids.append(ticket_id)
    if not ticket_ids:
        raise RuntimeError("大模型没有选出有效工单，请调整提示词后重试")

    reasons_raw = parsed.get("reasons")
    reasons: Dict[str, str] = {}
    if isinstance(reasons_raw, dict):
        for key, value in reasons_raw.items():
            text_value = str(value).strip()
            if text_value:
                reasons[str(key).strip()] = text_value

    summary = parsed.get("summary")
    return {
        "ticket_ids": ticket_ids[:limit],
        "summary": summary.strip() if isinstance(summary, str) else "",
        "reasons": reasons,
    }


# ── 服务 ───────────────────────────────────────────────

class ProjectTicketsService:
    """项目工单卡取数与 AI 阻滞配置。"""

    async def get_overview(self, db: AsyncSession, project_id: str) -> Dict[str, Any]:
        """工单概览：状态计数（复用仪表盘单项目口径）+ 近 8 周新建数 + 核心阻滞工单。"""
        summary = await task_dashboard_service.get_ticket_summary(db, [project_id])
        weekly = await self._weekly_trend(db, project_id)
        # get_user_map 是同步方法（进程内缓存），异步上下文经线程池调用
        user_map = await run_in_threadpool(user_service.get_user_map)
        blocking = await self._load_blocking(db, project_id, user_map)
        return {
            "total": summary["total"],
            "by_status": summary["by_status"],
            "pending_count": summary["pending_count"],
            "overdue_count": summary["overdue_count"],
            "resolved_rate": summary["resolved_rate"],
            "weekly": weekly,
            "blocking": blocking,
        }

    async def configure_blocking(self, db: AsyncSession, project_id: str, prompt: str,
                                 operator: Optional[str] = None,
                                 operator_name: Optional[str] = None) -> Dict[str, Any]:
        """「配置阻滞权重」：项目 + 工单基础数据 + 提示词 → 大模型判定 → 落库并返回阻滞板块。"""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("请先输入阻滞权重的判定要求")

        project = await run_in_threadpool(_get_project, project_id)
        if not project:
            raise LookupError("项目不存在")

        user_map = await run_in_threadpool(user_service.get_user_map)
        rows = await self._project_tasks(db, project_id, limit=AI_CANDIDATE_LIMIT)
        if not rows:
            raise ValueError("该项目暂无工单，无法判定阻滞权重")

        tickets = [self._ticket_for_ai(task, user_map) for task in rows]
        llm_prompt = build_blocking_prompt(project, tickets, prompt)

        # 与「AI 项目摘要」共用同一 LLM 客户端（app/core/llm_client.py，backend 自维护）：
        # 未配置 → LLMError → RuntimeError（接口层 503）；调用异常统一转可读信息
        client = get_llm_client()
        try:
            text = await client.complete(
                llm_prompt,
                system_prompt=_BLOCKING_SYSTEM_PROMPT,
                max_tokens=1200,
                temperature=0.2,
                thinking=False,
            )
        except Exception as exc:  # noqa: BLE001 —— 网络/鉴权/超时等统一转用户可读信息
            logger.error("[blocking-config] 大模型调用失败: project_id=%s, error=%s", project_id, exc)
            raise RuntimeError(f"大模型调用失败：{exc}") from exc

        parsed = parse_blocking_result(text, {task.id for task in rows})

        now = datetime.now()
        config = await db.get(ProjectBlockingConfig, project_id)
        if config is None:
            config = ProjectBlockingConfig(project_id=project_id)
            db.add(config)
        config.prompt = prompt
        config.ai_result = json.dumps(parsed, ensure_ascii=False)
        config.updated_by = operator
        config.updated_by_name = operator_name
        config.updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
        await db.commit()
        logger.info("[blocking-config] project_id=%s by=%s 选出工单=%s", project_id, operator, parsed["ticket_ids"])

        return await self._load_blocking(db, project_id, user_map)

    # ── 内部 ─────────────────────────────────────────

    async def _weekly_trend(self, db: AsyncSession, project_id: str) -> List[Dict[str, Any]]:
        now = datetime.now()
        window_start = week_start_of(now) - timedelta(days=7 * (TREND_WEEKS - 1))
        result = await db.execute(
            select(Task.created_at).where(
                Task.project_id == project_id,
                Task.created_at.isnot(None),
                Task.created_at >= window_start,
            )
        )
        return build_weekly_trend(list(result.scalars().all()), now)

    async def _project_tasks(self, db: AsyncSession, project_id: str, limit: Optional[int] = None) -> List[Task]:
        query = select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc(), Task.id.desc())
        if limit:
            query = query.limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _load_blocking(self, db: AsyncSession, project_id: str, user_map: Dict[str, str]) -> Dict[str, Any]:
        """核心阻滞板块：优先展示 AI 配置结果（工单按存储顺序回查，缺失剔除）；否则默认规则。"""
        config = await db.get(ProjectBlockingConfig, project_id)
        if config is not None and config.ai_result:
            try:
                parsed = json.loads(config.ai_result)
            except ValueError:
                parsed = None
            ids: List[int] = []
            if isinstance(parsed, dict):
                for value in parsed.get("ticket_ids") or []:
                    try:
                        ids.append(int(value))
                    except (TypeError, ValueError):
                        continue
            if ids:
                rows = (await db.execute(select(Task).where(Task.id.in_(ids)))).scalars().all()
                by_id = {task.id: task for task in rows}
                # 只保留仍存在、且仍属于该项目的工单（项目绑定被改过的不算）
                tickets = [
                    self._ticket_item(by_id[ticket_id], user_map)
                    for ticket_id in ids
                    if ticket_id in by_id and by_id[ticket_id].project_id == project_id
                ]
                if tickets:
                    return {
                        "mode": "ai",
                        "tickets": tickets,
                        "prompt": config.prompt,
                        "summary": parsed.get("summary") if isinstance(parsed.get("summary"), str) else "",
                        "reasons": parsed.get("reasons") if isinstance(parsed.get("reasons"), dict) else {},
                        "updated_by_name": config.updated_by_name,
                        "updated_at": config.updated_at,
                    }

        # 默认规则：未完成工单按「优先级 > 超期 > 最新」排序取前 N（配置损坏/工单全被删时也走这里）
        rows = (await db.execute(select(Task).where(
            Task.project_id == project_id,
            Task.status.in_(OPEN_FOR_BLOCKING),
        ))).scalars().all()
        tickets = [self._ticket_item(task, user_map) for task in default_blocking_order(list(rows))[:DEFAULT_BLOCKING_LIMIT]]
        return {
            "mode": "default",
            "tickets": tickets,
            "prompt": config.prompt if config is not None else None,
            "summary": "",
            "reasons": {},
            "updated_by_name": None,
            "updated_at": None,
        }

    def _ticket_item(self, task: Task, user_map: Dict[str, str]) -> Dict[str, Any]:
        """卡片条目的工单字段（含「问题概况」摘要与超期标记，前端不再自行比较时间）。"""
        description = " ".join((task.description or "").split())
        if len(description) > DESCRIPTION_PREVIEW_CHARS:
            description = description[:DESCRIPTION_PREVIEW_CHARS] + "…"
        overdue = bool(task.deadline_at and task.deadline_at < datetime.now()
                       and task.status in OPEN_FOR_BLOCKING)
        return {
            "id": task.id,
            "title": task.title,
            "status": task.status.value if task.status else "",
            "priority": task.priority.value if task.priority else "",
            "ticket_type": task.task_type.value if task.task_type else "",
            "created_by": task.created_by,
            "creator_name": user_map.get(task.created_by, task.created_by) if task.created_by else None,
            "assigned_to": task.assigned_to,
            "assignee_name": user_map.get(task.assigned_to, task.assigned_to) if task.assigned_to else None,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "deadline_at": task.deadline_at.isoformat() if task.deadline_at else None,
            "overdue": overdue,
            "description": description,
        }

    def _ticket_for_ai(self, task: Task, user_map: Dict[str, str]) -> Dict[str, Any]:
        """送大模型的单条工单（带中文标签与超期标记，描述截断）。"""
        status = task.status.value if task.status else ""
        priority = task.priority.value if task.priority else ""
        ticket_type = task.task_type.value if task.task_type else ""
        description = " ".join((task.description or "").split())
        if len(description) > AI_DESCRIPTION_CHARS:
            description = description[:AI_DESCRIPTION_CHARS] + "…"
        overdue = bool(task.deadline_at and task.deadline_at < datetime.now()
                       and task.status in OPEN_FOR_BLOCKING)
        return {
            "id": task.id,
            "title": task.title,
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "priority": priority,
            "priority_label": PRIORITY_LABELS.get(priority, priority),
            "ticket_type": ticket_type,
            "type_label": TYPE_LABELS.get(ticket_type, ticket_type),
            "created_at": task.created_at.strftime("%Y-%m-%d") if task.created_at else "",
            "deadline_at": task.deadline_at.strftime("%Y-%m-%d") if task.deadline_at else "",
            "overdue": overdue,
            "creator_name": user_map.get(task.created_by, task.created_by) if task.created_by else "",
            "assignee_name": user_map.get(task.assigned_to, task.assigned_to) if task.assigned_to else "",
            "description": description,
        }


def _get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """同步取项目（在线程池里调用）；延迟导入避免 admin 服务间循环依赖。"""
    from app.modules.admin.services.project_service import project_service

    return project_service.get_project(project_id)


project_tickets_service = ProjectTicketsService()
