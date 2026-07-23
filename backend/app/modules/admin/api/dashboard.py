"""admin 仪表盘 —— 工单状态监测统计（系统任务模块 Task 表）。

数据来源：backend/app/models/task.py 的 Task 表（tasks），
与 app/modules/admin/api/tickets.py 代理的 AI 服务 tickets 表是不同数据源，
契约详见 docs/工程文档.md §3.4、frontend/src/api/dashboard.ts。
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.modules.admin.services.task_dashboard_service import task_dashboard_service
from app.modules.admin.utils_das.config import security, DEBUG_MODE

dashboard_router = APIRouter(prefix="/dashboard", tags=["admin-dashboard"])


@dashboard_router.get("/tickets/summary", summary="工单状态汇总（系统任务模块）")
async def get_ticket_summary(
    db: AsyncSession = Depends(get_db),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    summary = await task_dashboard_service.get_ticket_summary(db)
    return {"code": 0, "data": summary}


@dashboard_router.get("/tickets", summary="按状态查询工单列表（系统任务模块）")
async def get_tickets_by_status(
    status: str = Query(..., description="状态key: new/in_progress/paused/resolved/closed/cancelled"),
    skip: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    result = await task_dashboard_service.get_tickets_by_status(db, status, skip, limit)
    return {"code": 0, "data": result}
