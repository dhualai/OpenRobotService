"""admin 仪表盘 —— 系统任务模块 Task 状态统计服务。

直接查询 app.models.task.Task（系统任务表 tasks），与 AI 服务 tickets 表统计
（见 app/modules/admin/api/tickets.py）是不同数据源，不可混用。
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import Counter

from sqlalchemy import select, func, and_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus, TaskOperationLog, OperationType
from app.core.user_identity import same_identity
from app.services.user_service import user_service

# 前端仪表盘状态 key -> 后端 TaskStatus 枚举值。
# "paused"/"cancelled" 复用 PENDING/CANCELED（对齐 zentao/mapper.py 的 pause/cancel 映射）。
FRONTEND_STATUS_MAP: Dict[str, TaskStatus] = {
    "new": TaskStatus.NEW,
    "in_progress": TaskStatus.IN_PROGRESS,
    "paused": TaskStatus.PENDING,
    "resolved": TaskStatus.RESOLVED,
    "closed": TaskStatus.CLOSED,
    "cancelled": TaskStatus.CANCELED,
}

# 仪表盘「工单状态监测」监控的五种状态（不含 new：新建工单不参与该看板的计数统计，
# 与前端 TICKET_STATUS_LIST 保持一致；下钻接口 get_tickets_by_status 仍支持查询 new）
MONITORED_STATUS_KEYS = ["in_progress", "paused", "resolved", "closed", "cancelled"]

# 超时工单统计的口径：仅监控中的未完成状态（new 不计入）
OPEN_STATUSES = [TaskStatus.IN_PROGRESS, TaskStatus.PENDING]


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

        by_status: Dict[str, int] = {}
        for key in MONITORED_STATUS_KEYS:
            status_enum = FRONTEND_STATUS_MAP[key]
            query = select(func.count(Task.id)).where(Task.status == status_enum)
            if project_ids is not None:
                query = query.where(Task.project_id.in_(project_ids))
            result = await db.execute(query)
            by_status[key] = result.scalar() or 0

        # 总数与状态分布同口径：仅统计监控中的五种状态（不含 new）
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

        # 解决率 =（已解决 + 已关闭 + 已取消）/ 总工单数（与上方 total 同口径，均不含 new）
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
        #   all     总工单数 = 监控中的五种状态（不含 new）
        #   pending 待处理   = 处理中 + 暂停/挂起
        #   overdue 超时工单 = 截止时间已过且仍处于未完成状态
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
        result = await db.execute(
            list_query.order_by(Task.created_at.desc()).offset(skip).limit(limit)
        )
        tasks = result.scalars().all()

        user_map = user_service.get_user_map()
        items = [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value if t.priority else "",
                "assignee_name": user_map.get(t.assigned_to, t.assigned_to) if t.assigned_to else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
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
        """
        if project_ids is not None and len(project_ids) == 0:
            return {"total": 0, "responded": 0, "by_bucket": []}

        # 1) 范围内工单：id / 处理人 / 创建时间
        task_query = select(Task.id, Task.assigned_to, Task.created_at)
        if project_ids is not None:
            task_query = task_query.where(Task.project_id.in_(project_ids))
        task_rows = (await db.execute(task_query)).all()
        total = len(task_rows)
        if total == 0:
            return {"total": 0, "responded": 0, "by_bucket": []}

        assignee_map = {row[0]: row[1] for row in task_rows}
        created_map = {row[0]: row[2] for row in task_rows}

        # 2) 这些工单的 VIEW 日志：每个 (task_id, operator) 取最早一次查看
        view_query = (
            select(
                TaskOperationLog.task_id,
                TaskOperationLog.operator,
                func.min(TaskOperationLog.created_at),
            )
            .where(
                TaskOperationLog.operation_type == OperationType.VIEW,
                TaskOperationLog.task_id.in_(list(assignee_map.keys())),
            )
            .group_by(TaskOperationLog.task_id, TaskOperationLog.operator)
        )
        view_rows = (await db.execute(view_query)).all()

        # 3) 只保留处理人的查看，取每单最早一条，计算差值并分桶
        first_view: Dict[int, datetime] = {}
        for task_id, operator, viewed_at in view_rows:
            if viewed_at is None:
                continue
            assignee = assignee_map.get(task_id)
            if not assignee or not same_identity(operator, assignee):
                continue  # 创建人/他人查看不算接单人响应
            cur = first_view.get(task_id)
            if cur is None or viewed_at < cur:
                first_view[task_id] = viewed_at

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
        """
        if project_ids is not None and len(project_ids) == 0:
            return {"by_type": []}

        query = select(Task.task_type, Task.created_at, Task.closed_at).where(
            Task.closed_at.isnot(None)
        )
        if project_ids is not None:
            query = query.where(Task.project_id.in_(project_ids))
        rows = (await db.execute(query)).all()

        elapsed_by_type: Dict[str, List[float]] = {}
        for task_type, created_at, closed_at in rows:
            if created_at is None or closed_at is None:
                continue
            elapsed = max((closed_at - created_at).total_seconds(), 0)
            elapsed_by_type.setdefault(task_type.value, []).append(elapsed)

        by_type = [
            {
                "key": key,
                "count": len(seconds_list),
                "avg_seconds": round(sum(seconds_list) / len(seconds_list)),
            }
            for key, seconds_list in elapsed_by_type.items()
        ]
        by_type.sort(key=lambda item: item["avg_seconds"], reverse=True)

        return {"by_type": by_type}


task_dashboard_service = TaskDashboardService()
