"""admin 日报管理 API（承接 DAS daily-reports）。

MIGRATION.md 阶段 3：从 `app/modules/das/api/daily_reports.py` 搬迁而来，
路由前缀从 `/api/DAS/daily-reports` 迁移到 `/api/admin/daily-reports`。
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, Dict, List
from app.modules.admin.schemas_das.request_models import DailyReportCreate, DailyReportUpdate, DailyReportResponse
from app.modules.admin.services.daily_report_service import daily_report_service
from app.modules.admin.utils_das.config import security, DEBUG_MODE

daily_report_router = APIRouter(prefix="/daily-reports", tags=["admin-daily-reports"])


@daily_report_router.get("/", summary="获取日报列表")
async def get_daily_reports(
    skip: int = Query(0, ge=0, description="跳过的日报数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的日报数"),
    project_code: Optional[str] = Query(None, description="按项目代码过滤"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> List[DailyReportResponse]:
    if project_code:
        reports = daily_report_service.get_reports_by_project(project_code, skip, limit)
    else:
        reports = daily_report_service.get_reports(skip, limit)
    return reports


@daily_report_router.get("/{report_id}", summary="获取单个日报")
async def get_daily_report(
    report_id: int,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> DailyReportResponse:
    report = daily_report_service.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="日报不存在")
    return report


@daily_report_router.get("/by-date/{project_code}/{report_date}", summary="根据项目代码和日期获取日报")
async def get_daily_report_by_date(
    project_code: str,
    report_date: str,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> DailyReportResponse:
    report = daily_report_service.get_report_by_date(project_code, report_date)
    if not report:
        raise HTTPException(status_code=404, detail="日报不存在")
    return report


@daily_report_router.post("/", summary="创建日报")
async def create_daily_report(
    report_data: DailyReportCreate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> DailyReportResponse:
    existing_report = daily_report_service.get_report_by_date(
        report_data.project_code,
        report_data.report_date
    )
    if existing_report:
        raise HTTPException(status_code=400, detail="该日期的日报已存在，请使用更新接口")
    
    report = daily_report_service.create_report(report_data.model_dump())
    return report


@daily_report_router.put("/{report_id}", summary="更新日报")
async def update_daily_report(
    report_id: int,
    update_data: DailyReportUpdate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> DailyReportResponse:
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    report = daily_report_service.update_report(report_id, update_dict)
    if not report:
        raise HTTPException(status_code=404, detail="日报不存在")
    return report


@daily_report_router.delete("/{report_id}", summary="删除日报")
async def delete_daily_report(
    report_id: int,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict[str, bool]:
    success = daily_report_service.delete_report(report_id)
    if not success:
        raise HTTPException(status_code=404, detail="日报不存在")
    return {"success": True}


@daily_report_router.get("/search/{keyword}", summary="搜索日报")
async def search_daily_reports(
    keyword: str,
    skip: int = Query(0, ge=0, description="跳过的日报数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的日报数"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> List[DailyReportResponse]:
    reports = daily_report_service.search_reports(keyword)
    return reports[skip:skip + limit]