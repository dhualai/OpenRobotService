"""admin 仪表盘 API —— 后台管理首页看板数据接口。

前端 Dashboard 页面调用的三个核心接口：
- GET /api/admin/dashboard/tickets/summary    → 工单状态汇总（饼图 + 统计卡）
- GET /api/admin/dashboard/tickets?status=xx   → 按状态筛选工单列表（下钻）
- GET /api/admin/dashboard/projects/summary    → 项目阶段汇总（饼图）
- GET /api/admin/dashboard/projects/urgency    → 项目紧急度汇总（四象限）

数据来源：
- 工单统计：系统任务模块 tasks 表（app.models.task.Task）
- 项目统计：admin 模块 projects 表（app.modules.admin.models_das.models.Project）

所有接口支持 project_ids 查询参数（逗号分隔），用于按当前用户关联项目过滤：
- 不传 project_ids：不过滤（向后兼容）
- 传 project_ids（即使为空）：仅统计指定项目内的数据
"""
import re
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, List, Tuple

from app.core.database import get_async_db as get_db
from app.models.delivery import UNDERTAKE_PENDING
from app.modules.admin.services.task_dashboard_service import task_dashboard_service
from app.modules.admin.services.project_service import project_service
from app.modules.admin.services.risk_service import risk_service
from app.modules.admin.services.transport_efficiency_service import transport_efficiency_service

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


def _parse_project_ids(raw: Optional[str]) -> Optional[List[str]]:
    """解析 project_ids 查询参数。

    返回值约定：
    - None：未传该参数，表示不限制（向后兼容）
    - 空列表：传了参数但为空，表示当前用户无关联项目
    - 非空列表：按这些项目 ID 过滤
    """
    if raw is None:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_settlement_period(period: Any) -> Optional[Tuple[int, int]]:
    """解析项目业绩核算期 settlement_period，返回 (年, 月)。

    该列为手工填写，常见写法：
    - YYYYMM   → 202608（2026年8月）
    - YYYY-MM  → 2026-08
    兼容分隔符 - / . 及个位月份（如 2026/8）。非法/缺失返回 None。
    """
    if period is None:
        return None
    m = re.fullmatch(r"(\d{4})[-/.]?(\d{1,2})", str(period).strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        return None
    return year, month


def _enrich_projects_with_analysis(projects: List[Dict]) -> None:
    """为项目列表就地补充分析字段，供下钻列表卡片展示。

    与 /projects/ 接口 include_analysis 逻辑保持一致：
    - risks：未关闭风险数（status != 关闭 计 1）
    - task_execution_stats：近 7 天任务统计
    - latest_manual_switch_count：最近切手动次数
    """
    project_codes = [p["project_code"] for p in projects]
    if not project_codes:
        return

    detailed_risks = risk_service.get_detailed_open_risks_by_project_codes(project_codes)

    for project in projects:
        project_code = project["project_code"]
        project_risks = detailed_risks.get(project_code, [])
        project["risks"] = sum(1 for risk in project_risks if risk.get("status") != "关闭")
        project["task_execution_stats"] = project_service.get_task_execution_stats_7d(project_code)
        project["latest_manual_switch_count"] = transport_efficiency_service.get_latest_manual_switch_count(project_code)


@dashboard_router.get("/tickets/summary", response_model=Dict[str, Any])
async def get_ticket_summary(
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅统计这些项目内的工单"),
    db: AsyncSession = Depends(get_db),
):
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
    pid_list = _parse_project_ids(project_ids)
    stats = await task_dashboard_service.get_ticket_summary(db, pid_list)
    return {"code": 0, "data": stats}


@dashboard_router.get("/tickets", response_model=Dict[str, Any])
async def get_tickets_by_status(
    status: Optional[str] = Query(None, description="工单状态key"),
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅返回这些项目内的工单"),
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

    pid_list = _parse_project_ids(project_ids)
    result = await task_dashboard_service.get_tickets_by_status(db, status, project_ids=pid_list)
    return {"code": 0, "data": result}


@dashboard_router.get("/projects/summary", response_model=Dict[str, Any])
async def get_project_stage_summary(
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅统计这些项目"),
):
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
    pid_list = _parse_project_ids(project_ids)
    if pid_list is not None:
        projects = project_service.get_projects_by_ids(pid_list)
    else:
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


@dashboard_router.get("/projects/monthly", response_model=Dict[str, Any])
async def get_project_monthly_summary(
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅统计这些项目"),
):
    """项目按月统计 —— 供仪表盘「跨项目看板」月柱状图使用（替换原按阶段统计的展示口径）。

    按月口径 = 项目业绩核算期 settlement_period（手工填写，常见 YYYYMM 如 202608 = 2026年8月，
    也兼容 YYYY-MM；模型字段已建索引），与「本月新增」统计卡口径一致；
    无核算期的项目不落在任何月份，不参与统计。输出统一归一化为 YYYY-MM 的 key。

    已承接（value）与待定（pending_value）分开计数，前端画成同一根柱子的深/浅两段。
    本接口是全站唯一放开 include_pending 的地方：待定项目只影响这张图，
    项目总数/项目列表/紧急度看板等口径均不含待定，见 project_service.get_projects。

    响应结构：
    {
        "code": 0,
        "data": {
            "monthly": [{"key": "2026-08", "year": 2026, "month": 8, "value": 12, "pending_value": 3}, ...],
            "years": [2024, 2025, 2026]
        }
    }
    """
    pid_list = _parse_project_ids(project_ids)
    if pid_list is not None:
        projects = project_service.get_projects_by_ids(pid_list, include_pending=True)
    else:
        projects = project_service.get_projects(0, 1000, include_pending=True)

    monthly_map: Dict[str, int] = {}
    pending_map: Dict[str, int] = {}
    for project in projects:
        parsed = _parse_settlement_period(project.get("settlement_period"))
        if parsed is None:
            continue
        year, month = parsed
        key = f"{year:04d}-{month:02d}"
        target = pending_map if project.get("undertake_status") == UNDERTAKE_PENDING else monthly_map
        target[key] = target.get(key, 0) + 1

    monthly = [
        {
            "key": key,
            "year": int(key[:4]),
            "month": int(key[5:]),
            "value": monthly_map.get(key, 0),
            "pending_value": pending_map.get(key, 0),
        }
        for key in set(monthly_map) | set(pending_map)
    ]
    monthly.sort(key=lambda item: item["key"])

    years = sorted({item["year"] for item in monthly})

    return {
        "code": 0,
        "data": {
            "monthly": monthly,
            "years": years,
        },
    }


@dashboard_router.get("/projects/urgency", response_model=Dict[str, Any])
async def get_project_urgency_summary(
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅统计这些项目"),
):
    """项目紧急度汇总 —— 供仪表盘「跨项目看板」紧急度四象限使用。

    响应结构：
    {
        "code": 0,
        "data": {
            "by_urgency": { "important_urgent": 10, ... }
        }
    }
    """
    pid_list = _parse_project_ids(project_ids)
    if pid_list is not None:
        projects = project_service.get_projects_by_ids(pid_list)
    else:
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
    project_ids: Optional[str] = Query(None, description="项目ID列表，逗号分隔；传入后仅返回这些项目"),
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
    pid_list = _parse_project_ids(project_ids)
    if pid_list is not None:
        all_projects = project_service.get_projects_by_ids(pid_list)
    else:
        all_projects = project_service.get_projects(0, 1000)

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

    _enrich_projects_with_analysis(filtered_projects)

    items = [
        {
            "id": str(p["id"]),
            "project_code": p["project_code"],
            "name": p["name"],
            "status": p["status"],
            "contact_person": p.get("contact_person", ""),
            "project_manager": p.get("project_manager", ""),
            "project_contact": p.get("project_contact", ""),
            "risks": p.get("risks", 0),
            "task_execution_stats": p.get("task_execution_stats"),
            "latest_manual_switch_count": p.get("latest_manual_switch_count"),
            "settlement_period": p.get("settlement_period"),
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
