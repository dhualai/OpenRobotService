"""直答率周报接口（ORS-#981：AI 直答率上服务号后台）。

数据链：本地 dar_weekly SOP 产出周报 → report 步骤自动 POST 本接口（写口
保留 backend:project:all——仅 admin 级账号推）→ 「工单数据看板」页 AI 直答率
区块读 latest/list 展示。payload 为 dar_weekly 周报原样 JSON；fails 明细
（含用户对话）存表但展示侧不透出。

读接口不设权限：与同页 /dashboard/summary-all 一致（那页所有数据口都靠前端
UI 权限门 frontend:admin:other:show 控制可见性）。曾要求 backend:project:all
——有 other:show 无 project:all 的角色会「看得到区块入口但数据静默为空」
（1016 实锤）。
"""
import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.models.dar_report import DarWeeklyReport
from app.modules.admin.api.auth import require_permission

from fastapi import Body

dar_report_router = APIRouter(prefix="/dashboard/dar-reports", tags=["admin-dashboard"])


def _summary(row: DarWeeklyReport) -> Dict[str, Any]:
    """payload 摘要（列表/趋势用）：不透出 fails 等明细。"""
    try:
        p = json.loads(row.payload or "{}")
    except Exception:
        p = {}
    return {
        "week_date": row.week_date,
        "env": row.env,
        "dar_rates": p.get("dar_rates") or {},
        "dar_rates_same_base": p.get("dar_rates_same_base") or {},
        "kb_gap": p.get("kb_gap"),
        "wow": p.get("wow") or {},
        "this_week": p.get("this_week") or {},
        "manual_progress": p.get("manual_progress"),
        # 工作台展示值快照（推送时由 dar_studio.metrics 原样取）——前端区块按这份渲染，
        # 保证与工作台同数；无快照时前端退回上面的周报口径字段
        "workbench": p.get("workbench"),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@dar_report_router.post("", summary="上传/更新直答率周报（dar_weekly report 步自动推）")
async def upsert_dar_report(
    body: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = require_permission("backend:project:all"),
):
    """body: {week_date: "YYYYMMDD", env: "prod|test", payload: <周报原样 JSON>}。

    同 (week_date, env) 已存在则整包覆盖（重复点生成周报幂等）。
    """
    week_date = str(body.get("week_date") or "").strip()
    env = str(body.get("env") or "prod").strip() or "prod"
    payload = body.get("payload")
    if not week_date or not isinstance(payload, dict):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="week_date 与 payload(dict) 必填")
    text = json.dumps(payload, ensure_ascii=False)

    row = (await db.execute(
        select(DarWeeklyReport).where(
            DarWeeklyReport.week_date == week_date, DarWeeklyReport.env == env)
    )).scalars().first()
    if row is None:
        row = DarWeeklyReport(week_date=week_date, env=env, payload=text)
        db.add(row)
    else:
        row.payload = text
    await db.commit()
    return {"code": 0, "data": {"id": row.id, "week_date": week_date, "env": env}}


@dar_report_router.get("/latest", summary="最新一期直答率周报（摘要，不含 fails 明细）")
async def latest_dar_report(
    env: str = Query("prod"),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(
        select(DarWeeklyReport).where(DarWeeklyReport.env == env)
        .order_by(DarWeeklyReport.week_date.desc()).limit(1)
    )).scalars().first()
    if row is None:
        return {"code": 0, "data": None}
    return {"code": 0, "data": _summary(row)}


@dar_report_router.get("/list", summary="直答率周报列表（趋势用，按周倒序）")
async def list_dar_reports(
    env: str = Query("prod"),
    limit: int = Query(12, ge=1, le=52),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(DarWeeklyReport).where(DarWeeklyReport.env == env)
        .order_by(DarWeeklyReport.week_date.desc()).limit(limit)
    )).scalars().all()
    return {"code": 0, "data": [_summary(r) for r in rows]}
