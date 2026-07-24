"""admin 仪表盘 —— 系统任务模块 Task 状态统计服务。

直接查询 app.models.task.Task（系统任务表 tasks），与 AI 服务 tickets 表统计
（见 app/modules/admin/api/tickets.py）是不同数据源，不可混用。
"""
from typing import Dict, Any
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

OPEN_STATUSES = [TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.PENDING]


class TaskDashboardService:
    @staticmethod
    async def get_ticket_summary(db: AsyncSession) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        for key, status_enum in FRONTEND_STATUS_MAP.items():
            result = await db.execute(select(func.count(Task.id)).where(Task.status == status_enum))
            by_status[key] = result.scalar() or 0

        total_result = await db.execute(select(func.count(Task.id)))
        total = total_result.scalar() or 0

        pending_count = by_status["new"] + by_status["in_progress"] + by_status["paused"]

        now = datetime.now()
        overdue_result = await db.execute(
            select(func.count(Task.id)).where(
                and_(
                    Task.deadline_at.isnot(None),
                    Task.deadline_at < now,
                    Task.status.in_(OPEN_STATUSES),
                )
            )
        )
        overdue_count = overdue_result.scalar() or 0

        resolved_rate = round(by_status["resolved"] / total, 4) if total else 0.0

        return {
            "total": total,
            "pending_count": pending_count,
            "overdue_count": overdue_count,
            "resolved_rate": resolved_rate,
            "by_status": by_status,
        }

    @staticmethod
    async def get_tickets_by_status(db: AsyncSession, status_key: str, skip: int = 0, limit: int = 20) -> Dict[str, Any]:
        status_enum = FRONTEND_STATUS_MAP.get(status_key)
        if status_enum is None:
            return {"items": [], "total": 0}

        count_result = await db.execute(select(func.count(Task.id)).where(Task.status == status_enum))
        total = count_result.scalar() or 0

        result = await db.execute(
            select(Task)
            .where(Task.status == status_enum)
            .order_by(Task.created_at.desc())
            .offset(skip)
            .limit(limit)
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
