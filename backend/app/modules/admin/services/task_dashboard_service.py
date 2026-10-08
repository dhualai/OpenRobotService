"""admin 仪表盘 —— 系统任务模块 Task 状态统计服务。

直接查询 app.models.task.Task（系统任务表 tasks），与 AI 服务 tickets 表统计
（见 app/modules/admin/api/tickets.py）是不同数据源，不可混用。
"""
import logging
import time
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timedelta
from collections import Counter

from sqlalchemy import select, func, and_, or_, case, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.task import Task, TaskStatus, TaskOperationLog, OperationType
from app.services.user_service import user_service

logger = logging.getLogger(__name__)

# 前端仪表盘状态 key -> 后端 TaskStatus 枚举值。
# "paused"/"cancelled" 复用 PENDING/CANCELED（对齐 zentao/mapper.py 的 pause/cancel 映射）。
FRONTEND_STATUS_MAP: Dict[str, TaskStatus] = {
    "new": TaskStatus.NEW,
    "in_progress": TaskStatus.IN_PROGRESS,
    "pending_requested": TaskStatus.PENDING_REQUESTED,
    "paused": TaskStatus.PENDING,
    "resolved": TaskStatus.RESOLVED,
    "closed": TaskStatus.CLOSED,
    "cancelled": TaskStatus.CANCELED,
}

# 仪表盘「工单状态监测」监控的状态（含 new：待处理工单计入工单总数与解决率分母，
# 与前端 TICKET_STATUS_LIST 保持一致；超时/待处理口径不含 new，见 OPEN_STATUSES）
MONITORED_STATUS_KEYS = ["new", "in_progress", "pending_requested", "paused", "resolved", "closed", "cancelled"]

# 超时工单统计的口径：未完成且已进入处理流程的状态（new 尚未开始处理，不计入）
OPEN_STATUSES = [TaskStatus.IN_PROGRESS, TaskStatus.PENDING_REQUESTED, TaskStatus.PENDING]


class TaskDashboardService:
    @staticmethod
    async def get_ticket_summary(
        db: AsyncSession,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # project_ids 为 None 表示不过滤；为空列表表示当前用户无关联项目，直接返回空统计
        if project_ids is not None and len(project_ids) == 0:
            return {
                "total": 0,
                "pending_count": 0,
                "overdue_count": 0,
                "resolved_rate": 0.0,
                "by_status": {key: 0 for key in MONITORED_STATUS_KEYS},
            }

        # 状态分布：一条 GROUP BY 取全部状态计数（原先按 6 个状态各发一条 COUNT）
        status_query = select(Task.status, func.count(Task.id)).group_by(Task.status)
        if project_ids is not None:
            status_query = status_query.where(Task.project_id.in_(project_ids))
        status_rows = (await db.execute(status_query)).all()
        status_counts = {status: count for status, count in status_rows}

        by_status: Dict[str, int] = {
            key: status_counts.get(status_enum, 0)
            for key, status_enum in FRONTEND_STATUS_MAP.items()
        }

        # 总数与状态分布同口径：监控中的六种状态之和（含 new）
        total = sum(by_status.values())

        pending_count = by_status["in_progress"] + by_status["paused"]

        now = datetime.now()
        overdue_query = select(func.count(Task.id)).where(
            and_(
                Task.deadline_at.isnot(None),
                Task.deadline_at < now,
                Task.status.in_(OPEN_STATUSES),
            )
        )
        if project_ids is not None:
            overdue_query = overdue_query.where(Task.project_id.in_(project_ids))
        overdue_result = await db.execute(overdue_query)
        overdue_count = overdue_result.scalar() or 0

        # 解决率 =（已解决 + 已关闭 + 已取消）/ 总工单数（分母含 new，与上方 total 同口径）
        resolved_rate = (
            round(
                (by_status["resolved"] + by_status["closed"] + by_status["cancelled"]) / total,
                4,
            )
            if total
            else 0.0
        )

        return {
            "total": total,
            "pending_count": pending_count,
            "overdue_count": overdue_count,
            "resolved_rate": resolved_rate,
            "by_status": by_status,
        }

    @staticmethod
    async def get_ticket_counts_by_project(
        db: AsyncSession,
        project_ids: List[str],
    ) -> Dict[str, int]:
        """按项目批量统计工单数（项目进度管理页项目卡右上角展示用）。

        口径与 get_ticket_summary 的 total 一致：监控中的六种状态之和。
        一条 GROUP BY 取全部项目，返回 {project_id: 工单数}；
        没有工单的项目不出现在结果里（调用方按 0 兜底）。
        """
        if not project_ids:
            return {}

        query = (
            select(Task.project_id, func.count(Task.id))
            .where(
                Task.project_id.in_(project_ids),
                Task.status.in_([FRONTEND_STATUS_MAP[key] for key in MONITORED_STATUS_KEYS]),
            )
            .group_by(Task.project_id)
        )
        rows = (await db.execute(query)).all()
        return {project_id: count for project_id, count in rows}

    @staticmethod
    async def get_tickets_by_status(
        db: AsyncSession,
        status_key: str,
        skip: int = 0,
        limit: int = 20,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if project_ids is not None and len(project_ids) == 0:
            return {"items": [], "total": 0}

        # 组合 scope key（对应仪表盘统计卡下钻，与 get_ticket_summary 同口径）：
        #   all     总工单数 = 监控中的六种状态（含 new）
        #   pending 待处理   = 处理中 + 暂停/挂起
        #   overdue 超时工单 = 截止时间已过且仍处于未完成状态（挂起置顶 + 超时最久在前，见下方 order_by）
        if status_key == "all":
            filters = [Task.status.in_([FRONTEND_STATUS_MAP[k] for k in MONITORED_STATUS_KEYS])]
        elif status_key == "pending":
            filters = [Task.status.in_(OPEN_STATUSES)]
        elif status_key == "overdue":
            filters = [
                Task.deadline_at.isnot(None),
                Task.deadline_at < datetime.now(),
                Task.status.in_(OPEN_STATUSES),
            ]
        else:
            status_enum = FRONTEND_STATUS_MAP.get(status_key)
            if status_enum is None:
                return {"items": [], "total": 0}
            filters = [Task.status == status_enum]

        count_query = select(func.count(Task.id)).where(*filters)
        if project_ids is not None:
            count_query = count_query.where(Task.project_id.in_(project_ids))
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        list_query = select(Task).where(*filters)
        if project_ids is not None:
            list_query = list_query.where(Task.project_id.in_(project_ids))
        # 超时工单排序：挂起工单（TaskStatus.PENDING，前端「暂停/挂起」）始终置顶，
        # 组内与其余工单均按「超时最久」在前 —— deadline_at 越早超时越久，故升序。
        # 本 scope 的过滤条件已保证 deadline_at 非空，无 NULL 排序歧义。
        # 其余 scope 维持创建时间倒序。
        order_by = (
            (case((Task.status == TaskStatus.PENDING, 0), else_=1), Task.deadline_at.asc())
            if status_key == "overdue"
            else (Task.created_at.desc(),)
        )
        result = await db.execute(
            list_query.order_by(*order_by).offset(skip).limit(limit)
        )
        tasks = result.scalars().all()

        # 同步 pymysql 加载，线程池执行避免阻塞事件循环（缓存未命中时才真正查库）
        user_map = await run_in_threadpool(user_service.get_user_map)
        items = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value if t.priority else "",
                "assignee_name": user_map.get(t.assigned_to, t.assigned_to) if t.assigned_to else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                # 截止时间：超时工单列表据此展示「已超时 X」（排序也在后端按此字段完成）
                "deadline_at": t.deadline_at.isoformat() if t.deadline_at else None,
            }
            for t in tasks
        ]
        return {"items": items, "total": total}

    # 角色分布饼图最多展示的角色数；超出部分并入「其他」
    ROLE_DISPLAY_LIMIT = 8

    @staticmethod
    async def get_source_analysis(
        db: AsyncSession,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """工单数据来源分析 —— 工单类型分布 + 提单人角色分布，供「工单数据来源分析」看板。

        - 类型分布：按 task_type（problem/feature/bug/support/other）分组计数，
          统计全部工单（含 new，不做状态过滤：来源分析不关心状态）。
        - 角色分布：对每个提单人取「主角色」（system 系统角色优先，否则取第一个
          project 项目角色；无角色归入「未分配角色」），按角色名分组计数。
          每个提单人只计入一个角色，避免一人多角色导致重复计数。
        """
        if project_ids is not None and len(project_ids) == 0:
            return {"by_type": [], "by_role": []}

        # 1) 工单类型分布
        type_query = select(Task.task_type, func.count(Task.id)).group_by(Task.task_type)
        if project_ids is not None:
            type_query = type_query.where(Task.project_id.in_(project_ids))
        type_rows = (await db.execute(type_query)).all()
        by_type = [{"key": k.value, "count": c} for k, c in type_rows]

        # 2) 提单人角色分布
        creator_query = select(distinct(Task.created_by))
        if project_ids is not None:
            creator_query = creator_query.where(Task.project_id.in_(project_ids))
        creator_rows = (await db.execute(creator_query)).all()
        creator_ids = [r[0] for r in creator_rows if r[0]]

        role_map: Dict[str, List[str]] = {}
        if creator_ids:
            from app.models.identity import user_project_roles, Role
            role_query = (
                select(user_project_roles.c.user_id, Role.name)
                .join(Role, Role.id == user_project_roles.c.role_id)
                .where(
                    user_project_roles.c.user_id.in_(creator_ids),
                    Role.role_type == "system",
                )
            )
            system_rows = (await db.execute(role_query)).all()
            for uid, rname in system_rows:
                role_map.setdefault(uid, []).append(rname)

            project_role_query = (
                select(user_project_roles.c.user_id, Role.name)
                .join(Role, Role.id == user_project_roles.c.role_id)
                .where(
                    user_project_roles.c.user_id.in_(creator_ids),
                    Role.role_type == "project",
                )
            )
            project_rows = (await db.execute(project_role_query)).all()
            for uid, rname in project_rows:
                role_map.setdefault(uid, []).append(rname)

        role_counter: Counter = Counter()
        for uid in creator_ids:
            roles = role_map.get(uid, [])
            if not roles:
                role_counter["未分配角色"] += 1
                continue
            # 主角色：system 优先，否则取第一个 project 角色
            role_counter[roles[0]] += 1

        # 只展示 Top N，其余并入「其他」，避免饼图图例过长
        top = role_counter.most_common(TaskDashboardService.ROLE_DISPLAY_LIMIT - 1)
        rest_count = sum(role_counter.values()) - sum(c for _, c in top)
        by_role = [{"label": name, "count": c} for name, c in top]
        if rest_count > 0:
            by_role.append({"label": "其他", "count": rest_count})

        return {"by_type": by_type, "by_role": by_role}

    # 接单人响应时间分桶（秒）：≤15分钟 / ≤1小时 / ≤4小时 / 其他（>4小时）
    RESPONSE_BUCKETS = {
        "within_15m": (0, 15 * 60, "15分钟内"),
        "within_1h": (15 * 60, 60 * 60, "1h内"),
        "within_4h": (60 * 60, 4 * 60 * 60, "4h内"),
        "other": (4 * 60 * 60, None, "其他"),
    }

    @staticmethod
    async def get_response_time_analysis(
        db: AsyncSession,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """接单人响应时间分析 —— 处理人第一次点开工单时间 与 新建工单时间的差值。

        响应时间口径（对应工单详情页「工单动态」中的查看记录）：
        - 处理人 = 工单 assigned_to（与 operation_log_service.get_role_prefix 同用
          same_identity 判断，保证与动态里【处理人】前缀的展示口径一致）；
        - 第一次点开 = 该处理人在本工单上的最早一条 VIEW 操作日志
          （log_view 自带 5 分钟去重，同一查看会话不会重复计数）；
        - 差值 = VIEW 时间 - 工单 created_at，按 RESPONSE_BUCKETS 分桶。

        未指派处理人 / 处理人从未点开过的工单不参与分桶（不计入 responded），
        只计入 total，避免「没点开」被误读为「响应极慢」。

        性能设计：SQL 只负责取数——VIEW 日志 JOIN 工单表一次带出
        assigned_to / created_at，不做 MIN/GROUP BY 聚合、不用巨型 task_id IN
        列表；每单最早一次查看、处理人匹配、耗时计算与分桶全部在 Python 侧
        完成，避免 MySQL 对大 IN + GROUP BY 走临时表/filesort。
        """
        if project_ids is not None and len(project_ids) == 0:
            return {"total": 0, "responded": 0, "by_bucket": []}

        # 1) 范围内工单总数（仅 COUNT，不拉全量工单行）
        t0 = time.monotonic()
        total_query = select(func.count(Task.id))
        if project_ids is not None:
            total_query = total_query.where(Task.project_id.in_(project_ids))
        total = (await db.execute(total_query)).scalar() or 0
        logger.info(
            "[dashboard response-time] COUNT 工单总数=%s 耗时 %.0fms (scope=%s)",
            total,
            (time.monotonic() - t0) * 1000,
            "all" if project_ids is None else f"{len(project_ids)}个项目",
        )
        if total == 0:
            return {"total": 0, "responded": 0, "by_bucket": []}

        # 2) VIEW 日志原始行：JOIN 工单表补齐处理人与创建时间，纯取数不聚合
        t1 = time.monotonic()
        view_query = (
            select(
                TaskOperationLog.task_id,
                TaskOperationLog.operator,
                TaskOperationLog.created_at,
                Task.assigned_to,
                Task.created_at,
            )
            .join(Task, Task.id == TaskOperationLog.task_id)
            .where(TaskOperationLog.operation_type == OperationType.VIEW)
        )
        if project_ids is not None:
            view_query = view_query.where(Task.project_id.in_(project_ids))
        view_rows = (await db.execute(view_query)).all()
        logger.info(
            "[dashboard response-time] JOIN 取 VIEW 日志 %d 行 耗时 %.0fms",
            len(view_rows),
            (time.monotonic() - t1) * 1000,
        )

        # 2b) 预建操作人/处理人身份映射：一次批量查 users 表拿 id/username，
        #     热循环不再逐行调 same_identity（该函数字符串不等时会同步查库 2 次，
        #     曾导致 5870 行日志 Python 计算耗时 35s+）
        t1b = time.monotonic()
        operators = {row[1] for row in view_rows if row[1]}
        assignees = {row[3] for row in view_rows if row[3]}
        identity_values = operators | assignees
        identity_map: Dict[str, Set[str]] = {}
        if identity_values:
            from app.models.identity import UserDB
            values = list(identity_values)
            user_rows = (
                await db.execute(
                    select(UserDB.id, UserDB.username).where(
                        or_(UserDB.username.in_(values), UserDB.id.in_(values))
                    )
                )
            ).all()
            key_sets: Dict[str, Set[str]] = {}
            for uid, uname in user_rows:
                keys = {uid, uname}
                key_sets[uid] = keys
                key_sets[uname] = keys
            for v in values:
                identity_map[v] = key_sets.get(v, {v})
        logger.info(
            "[dashboard response-time] 批量取用户身份 %d 个值耗时 %.0fms",
            len(identity_values),
            (time.monotonic() - t1b) * 1000,
        )

        # 3) Python 侧计算：只保留处理人本人的查看，取每单最早一条
        t2 = time.monotonic()
        first_view: Dict[int, datetime] = {}
        created_map: Dict[int, datetime] = {}
        for task_id, operator, viewed_at, assignee, task_created in view_rows:
            if viewed_at is None:
                continue
            if not assignee:
                continue
            # 身份互认（id/username 均认），与 same_identity 同语义但纯内存比较
            if not (identity_map.get(operator, {operator}) & identity_map.get(assignee, {assignee})):
                continue  # 创建人/他人查看不算接单人响应
            cur = first_view.get(task_id)
            if cur is None or viewed_at < cur:
                first_view[task_id] = viewed_at
                created_map[task_id] = task_created

        bucket_counts: Dict[str, int] = {key: 0 for key in TaskDashboardService.RESPONSE_BUCKETS}
        for task_id, viewed_at in first_view.items():
            created = created_map.get(task_id)
            if created is None:
                continue
            elapsed = max((viewed_at - created).total_seconds(), 0)
            for key, (_lo, hi, _label) in TaskDashboardService.RESPONSE_BUCKETS.items():
                if hi is None or elapsed <= hi:
                    bucket_counts[key] += 1
                    break

        by_bucket = [
            {"key": key, "label": label, "count": bucket_counts[key]}
            for key, (_lo, _hi, label) in TaskDashboardService.RESPONSE_BUCKETS.items()
        ]
        logger.info(
            "[dashboard response-time] Python 计算/分桶耗时 %.0fms, responded=%d",
            (time.monotonic() - t2) * 1000,
            len(first_view),
        )

        return {
            "total": total,
            "responded": len(first_view),
            "by_bucket": by_bucket,
        }

    @staticmethod
    async def get_avg_close_time_analysis(
        db: AsyncSession,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """各类型工单平均完单耗时 —— 按 task_type 分组统计「关闭时间 - 创建时间」的平均值。

        完单耗时口径 = closed_at - created_at，仅统计已关闭工单（closed_at 非空，
        与类型分布一致不按状态过滤：未关闭的工单没有完单时间，不计入即不会被误算为 0 耗时）。
        结果按平均耗时降序排列，方便前端横向条形图「最长的在最上面」。

        SQL 端聚合：AVG(GREATEST(0, TIMESTAMPDIFF(SECOND, created_at, closed_at)))，
        避免把全部已关闭工单行拉回 Python 逐行计算；TIMESTAMPDIFF 按整秒截断，
        与原先逐行 total_seconds() 的差异在亚秒级，可忽略。GREATEST(0, ...) 对应
        原来的 max(elapsed, 0) 负值钳制。
        """
        if project_ids is not None and len(project_ids) == 0:
            return {"by_type": []}

        elapsed_seconds = func.greatest(
            0,
            func.timestampdiff(text("SECOND"), Task.created_at, Task.closed_at),
        )
        query = (
            select(Task.task_type, func.count(Task.id), func.avg(elapsed_seconds))
            .where(Task.closed_at.isnot(None))
            .group_by(Task.task_type)
        )
        if project_ids is not None:
            query = query.where(Task.project_id.in_(project_ids))
        rows = (await db.execute(query)).all()

        by_type = [
            {
                "key": task_type.value,
                "count": count,
                "avg_seconds": round(float(avg_seconds)) if avg_seconds is not None else 0,
            }
            for task_type, count, avg_seconds in rows
        ]
        by_type.sort(key=lambda item: item["avg_seconds"], reverse=True)

        return {"by_type": by_type}


task_dashboard_service = TaskDashboardService()
