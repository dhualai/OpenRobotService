"""admin 仪表盘 API —— 后台管理首页看板数据接口。

前端 Dashboard 页面调用的三个核心接口：
- GET /api/admin/dashboard/tickets/summary    → 工单状态汇总（饼图 + 统计卡）
- GET /api/admin/dashboard/tickets?status=xx   → 按状态筛选工单列表（下钻）
- GET /api/admin/dashboard/projects/summary    → 项目阶段汇总（饼图）
- GET /api/admin/dashboard/projects/urgency    → 项目紧急度汇总（四象限）

数据来源：
- 工单统计：系统任务模块 tasks 表（app.models.task.Task）
- 项目统计：admin 模块 projects 表（app.modules.admin.models_das.models.Project）
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.database import get_async_db as get_db
from app.modules.admin.services.task_dashboard_service import task_dashboard_service
from app.modules.admin.services.project_service import project_service

dashboard_router = APIRouter(prefix="/dashboard", tags=["admin-dashboard"])

PROJECT_STAGE_MAP = {
    "pre_sales": ["售前方案"],
    "bidding": ["投标阶段"],
    "negotiation": ["签单洽谈"],
    "contract_signed": ["已签合同"],
    "factory_test": ["出厂测试"],
    "pending_entry": ["即将进场"],
    "delayed_entry": ["延期进场"],
    "in_implementation": ["正在实施"],
    "implementation_suspended": ["实施暂停"],
    "in_trial_operation": ["试运行中"],
    "acceptance_operation": ["验收运营"],
    "project_suspended": ["项目暂停", "项目中止"],
    "project_terminated": ["项目终止"],
    "project_changed": ["项目变更"],
    "project_ended": ["项目结束"],
}

URGENCY_MAP = {
    "important_urgent": ["重要紧急"],
    "urgent_not_important": ["紧急不重要"],
    "important_not_urgent": ["重要不紧急"],
    "not_important_not_urgent": ["不重要不紧急"],
}


@dashboard_router.get("/tickets/summary", response_model=Dict[str, Any])
async def get_ticket_summary(db: AsyncSession = Depends(get_db)):
    """工单状态汇总 —— 供仪表盘「工单状态监测」饼图和统计卡使用。
    
    响应结构：
    {
        "code": 0,
        "data": {
            "total": 100,
            "pending_count": 50,
            "overdue_count": 5,
            "resolved_rate": 0.82,
            "by_status": { "new": 10, "in_progress": 20, ... }
        }
    }
    """
    stats = await task_dashboard_service.get_ticket_summary(db)
    return {"code": 0, "data": stats}


@dashboard_router.get("/tickets", response_model=Dict[str, Any])
async def get_tickets_by_status(
    status: Optional[str] = Query(None, description="工单状态key"),
    db: AsyncSession = Depends(get_db),
):
    """按状态筛选工单列表 —— 点击状态标签下钻时调用。
    
    响应结构：
    {
        "code": 0,
        "data": {
            "items": [...],
            "total": 10
        }
    }
    """
    if not status:
        return {"code": 0, "data": {"items": [], "total": 0}}
    
    result = await task_dashboard_service.get_tickets_by_status(db, status)
    return {"code": 0, "data": result}


@dashboard_router.get("/projects/summary", response_model=Dict[str, Any])
async def get_project_stage_summary():
    """项目阶段汇总 —— 供仪表盘「跨项目看板」阶段饼图使用。
    
    响应结构：
    {
        "code": 0,
        "data": {
            "total": 50,
            "by_stage": { "pre_sales": 5, "in_implementation": 20, ... }
        }
    }
    """
    projects = project_service.get_projects(0, 1000)
    
    by_stage: Dict[str, int] = {key: 0 for key in PROJECT_STAGE_MAP.keys()}
    total = len(projects)
    
    for project in projects:
        project_status = project.get("status", "")
        for stage_key, status_labels in PROJECT_STAGE_MAP.items():
            if project_status in status_labels:
                by_stage[stage_key] += 1
                break
    
    return {
        "code": 0,
        "data": {
            "total": total,
            "by_stage": by_stage,
        },
    }


@dashboard_router.get("/projects/urgency", response_model=Dict[str, Any])
async def get_project_urgency_summary():
    """项目紧急度汇总 —— 供仪表盘「跨项目看板」紧急度四象限使用。
    
    响应结构：
    {
        "code": 0,
        "data": {
            "by_urgency": { "important_urgent": 10, ... }
        }
    }
    """
    projects = project_service.get_projects(0, 1000)
    
    by_urgency: Dict[str, int] = {key: 0 for key in URGENCY_MAP.keys()}
    
    for project in projects:
        category = project.get("category_basis", "")
        for urgency_key, category_labels in URGENCY_MAP.items():
            if category in category_labels:
                by_urgency[urgency_key] += 1
                break
    
    return {
        "code": 0,
        "data": {
            "by_urgency": by_urgency,
        },
    }


@dashboard_router.get("/projects", response_model=Dict[str, Any])
async def get_projects_by_stage_or_urgency(
    stage: Optional[str] = Query(None, description="项目阶段key"),
    urgency: Optional[str] = Query(None, description="紧急度key"),
):
    """按阶段或紧急度筛选项目列表 —— 点击阶段/紧急度标签下钻时调用。
    
    响应结构：
    {
        "code": 0,
        "data": {
            "items": [...],
            "total": 10
        }
    }
    """
    all_projects = project_service.get_projects(0, 1000)
    filtered_projects = []
    
    if stage:
        status_labels = PROJECT_STAGE_MAP.get(stage, [])
        filtered_projects = [
            p for p in all_projects
            if p.get("status", "") in status_labels
        ]
    elif urgency:
        category_labels = URGENCY_MAP.get(urgency, [])
        filtered_projects = [
            p for p in all_projects
            if p.get("category_basis", "") in category_labels
        ]
    else:
        filtered_projects = all_projects
    
    items = [
        {
            "id": str(p["id"]),
            "project_code": p["project_code"],
            "name": p["name"],
            "status": p["status"],
            "contact_person": p.get("contact_person", ""),
        }
        for p in filtered_projects
    ]
    
    return {
        "code": 0,
        "data": {
            "items": items,
            "total": len(items),
        },
    }