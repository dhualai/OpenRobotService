"""公司工作日历管理：工作时间 + 法定节假日/调休补班。

范围（已与需求方确认）：
- 仅公司级，不做部门/用户级覆盖
- 工作时段单段（不支持午休分段），无夜班
- 节假日由管理员在管理端手工维护，不做第三方自动同步
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import db_manager
from app.models.organization import Company
from app.models.work_calendar import CompanyHoliday
from app.modules.admin.api.auth import get_current_active_user_from_token, require_permission
from app.services.identity_service import IdentityService
from app.services.working_time_service import WorkingTimeService

router = APIRouter(tags=["公司工作日历"])

VALID_HOLIDAY_TYPES = {"holiday", "workday_override"}

PERM_CODE = "frontend:admin:work-calendar:manage"


def ensure_work_calendar_permission() -> None:
    """确保权限码已注册到 permissions 表（幂等），管理员可在「权限管理」中分配给他人。

    与 task_policy / wecom_sheets 同一套路：admin 直通，非管理员需显式授权。
    """
    IdentityService.add_permission(
        permission_id="perm_frontend_admin_work_calendar_manage",
        code=PERM_CODE,
        name="公司工作日历管理",
        resource_type="frontend",
        action="manage",
        description="后台「其他」中配置公司工作时间与法定节假日/调休（工单提醒按接收人工作时间推送）",
    )


def _valid_hhmm(value: str) -> bool:
    try:
        hh, mm = str(value).split(":")[:2]
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:
        return False


def _valid_workdays(value: str) -> bool:
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    return bool(parts) and all(p.isdigit() and 1 <= int(p) <= 7 for p in parts)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        raise HTTPException(status_code=400, detail=f"非法日期: {value}（需 yyyy-MM-dd）")


class WorkCalendarUpdate(BaseModel):
    work_start: Optional[str] = Field(None, description="工作开始 HH:MM")
    work_end: Optional[str] = Field(None, description="工作结束 HH:MM")
    workdays: Optional[str] = Field(None, description="工作日，如 1,2,3,4,5（1=周一）")


class HolidayItem(BaseModel):
    date: str = Field(..., description="yyyy-MM-dd")
    name: Optional[str] = Field(None, description="名称，如 国庆节")
    type: str = Field("holiday", description="holiday=放假 / workday_override=调休补班")


@router.get("/companies/{company_id}/work-calendar", summary="读取公司工作日历")
async def get_work_calendar(
    company_id: str,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    db = db_manager.get_db()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="公司不存在")
        return {
            "company_id": company.id,
            "company_name": company.name,
            "work_start": company.work_start or "09:00",
            "work_end": company.work_end or "18:00",
            "workdays": company.workdays or "1,2,3,4,5",
        }
    finally:
        db.close()


@router.put("/companies/{company_id}/work-calendar", summary="更新公司工作日历")
async def update_work_calendar(
    company_id: str,
    data: WorkCalendarUpdate,
    current_user: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_work_calendar_permission()

    if data.work_start and not _valid_hhmm(data.work_start):
        raise HTTPException(status_code=400, detail="work_start 需为 HH:MM")
    if data.work_end and not _valid_hhmm(data.work_end):
        raise HTTPException(status_code=400, detail="work_end 需为 HH:MM")
    if data.workdays and not _valid_workdays(data.workdays):
        raise HTTPException(status_code=400, detail="workdays 需为 1-7 的逗号分隔列表")

    if data.work_start and data.work_end and data.work_start >= data.work_end:
        raise HTTPException(status_code=400, detail="work_start 必须早于 work_end（不支持跨午夜/夜班）")

    db = db_manager.get_db()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="公司不存在")
        if data.work_start is not None:
            company.work_start = data.work_start
        if data.work_end is not None:
            company.work_end = data.work_end
        if data.workdays is not None:
            company.workdays = data.workdays
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新工作日历失败: {e}")
    finally:
        db.close()

    WorkingTimeService.invalidate_calendar_cache(company_id)
    return {"code": 200, "message": "工作日历已更新"}


@router.get("/companies/{company_id}/holidays", summary="节假日/调休列表")
async def list_holidays(
    company_id: str,
    year: Optional[int] = Query(None, description="按年份过滤，如 2026"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    db = db_manager.get_db()
    try:
        query = db.query(CompanyHoliday).filter(CompanyHoliday.company_id == company_id)
        if year:
            query = query.filter(
                CompanyHoliday.holiday_date >= date(year, 1, 1),
                CompanyHoliday.holiday_date <= date(year, 12, 31),
            )
        rows = query.order_by(CompanyHoliday.holiday_date.asc()).all()
        return [
            {
                "id": r.id,
                "date": r.holiday_date.isoformat() if r.holiday_date else None,
                "name": r.name,
                "type": r.type,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("/companies/{company_id}/holidays", summary="新增节假日/调休")
async def create_holiday(
    company_id: str,
    data: HolidayItem,
    current_user: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_work_calendar_permission()
    if data.type not in VALID_HOLIDAY_TYPES:
        raise HTTPException(status_code=400, detail="type 需为 holiday 或 workday_override")

    day = _parse_date(data.date)
    db = db_manager.get_db()
    try:
        exists = (
            db.query(CompanyHoliday)
            .filter(
                CompanyHoliday.company_id == company_id,
                CompanyHoliday.holiday_date == day,
            )
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail=f"{day.isoformat()} 已存在，请改用批量接口覆盖")

        row = CompanyHoliday(
            id=f"chd_{uuid4().hex}",
            company_id=company_id,
            holiday_date=day,
            name=data.name,
            type=data.type,
            created_by=current_user.get("username"),
        )
        db.add(row)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"新增节假日失败: {e}")
    finally:
        db.close()

    WorkingTimeService.invalidate_calendar_cache(company_id)
    return {"code": 200, "message": "已新增", "date": day.isoformat()}


@router.post("/companies/{company_id}/holidays/batch", summary="批量导入节假日/调休（同日期覆盖）")
async def batch_upsert_holidays(
    company_id: str,
    payload: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_work_calendar_permission()
    items: List[Dict[str, Any]] = payload.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="items 不能为空")

    db = db_manager.get_db()
    created = updated = 0
    try:
        for raw in items:
            if not isinstance(raw, dict) or not raw.get("date"):
                continue
            htype = raw.get("type") or "holiday"
            if htype not in VALID_HOLIDAY_TYPES:
                continue
            day = _parse_date(raw["date"])
            existing = (
                db.query(CompanyHoliday)
                .filter(
                    CompanyHoliday.company_id == company_id,
                    CompanyHoliday.holiday_date == day,
                )
                .first()
            )
            if existing:
                existing.name = raw.get("name")
                existing.type = htype
                updated += 1
            else:
                db.add(
                    CompanyHoliday(
                        id=f"chd_{uuid4().hex}",
                        company_id=company_id,
                        holiday_date=day,
                        name=raw.get("name"),
                        type=htype,
                        created_by=current_user.get("username"),
                    )
                )
                created += 1
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"批量导入失败: {e}")
    finally:
        db.close()

    WorkingTimeService.invalidate_calendar_cache(company_id)
    return {"code": 200, "message": "导入完成", "created": created, "updated": updated}


@router.delete("/companies/{company_id}/holidays/{holiday_id}", summary="删除节假日/调休")
async def delete_holiday(
    company_id: str,
    holiday_id: str,
    current_user: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_work_calendar_permission()
    db = db_manager.get_db()
    try:
        row = (
            db.query(CompanyHoliday)
            .filter(
                CompanyHoliday.id == holiday_id,
                CompanyHoliday.company_id == company_id,
            )
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.delete(row)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    finally:
        db.close()

    WorkingTimeService.invalidate_calendar_cache(company_id)
    return {"code": 200, "message": "已删除"}
