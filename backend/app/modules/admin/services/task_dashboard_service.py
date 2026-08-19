"""admin 仪表盘 —— 系统任务模块 Task 状态统计服务。

直接查询 app.models.task.Task（系统任务表 tasks），与 AI 服务 tickets 表统计
（见 app/modules/admin/api/tickets.py）是不同数据源，不可混用。
"""
from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
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
        status_enum = FRONTEND_STATUS_MAP.get(status_key)
        if status_enum is None:
            return {"items": [], "total": 0}

        if project_ids is not None and len(project_ids) == 0:
            return {"items": [], "total": 0}

        count_query = select(func.count(Task.id)).where(Task.status == status_enum)
        if project_ids is not None:
            count_query = count_query.where(Task.project_id.in_(project_ids))
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        list_query = select(Task).where(Task.status == status_enum)
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
                "status": status_key,
                "priority": t.priority.value if t.priority else "",
                "assignee_name": user_map.get(t.assigned_to, t.assigned_to) if t.assigned_to else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ]
        return {"items": items, "total": total}


task_dashboard_service = TaskDashboardService()
