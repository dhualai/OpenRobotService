"""企业微信表格数据源管理（M1：镜像层）。

页面在后台「其他」→「企微表格」，能力：
  - CRUD 一张企微智能表格的连接配置（docid / sheet_id）
  - 「新建智能表格」：调企微 create_doc 建表，拿回 docid（唯一获取途径）并落库
  - 「测试连接」：不落库，拉少量记录回真实列名 + 样例行（新增向导第 1 步）
  - 「立即同步」：把表格全量镜像进 external_record，返回 created/updated/unchanged
  - 查看镜像数据：确认接进来的东西对不对

鉴权分两套，与既有约定一致：
  - 管理接口走用户 JWT + `frontend:admin:wecom-sheets:show`（挂 /api/admin）
  - /sync-all 走 X-API-Key（挂 /api，供 Airflow 定时触发）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, engine
from app.integrations.api import verify_sync_api_key
from app.integrations.wecom_sheet.client import WecomSheetClient, WecomSheetClientError
from app.integrations.wecom_sheet.mirror import run_sync, run_sync_all
from app.models.wecom_sheet import ExternalRecord, WecomSheetSource
from app.modules.admin.api.auth import require_permission
from app.modules.admin.schemas.response import DataResponse
from app.services.identity_service import IdentityService

PERM = "frontend:admin:wecom-sheets:show"

admin_router = APIRouter(prefix="/wecom-sheets", tags=["admin-wecom-sheets"])
public_router = APIRouter(prefix="/wecom-sheets", tags=["wecom-sheets"])

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_TARGET_MODES = ("mirror", "handler")

_tables_ready = False


def ensure_wecom_sheets_permission() -> None:
    """权限管理里能勾到这一项；已存在则跳过。"""
    IdentityService.add_permission(
        permission_id="perm_frontend_admin_wecom-sheets_show",
        code=PERM,
        name="显示企微表格管理",
        resource_type="frontend",
        action="show",
        description="后台「其他」中显示企业微信表格数据源管理（新建智能表格、维护 docid、测试连接、立即同步）",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _open() -> AsyncSession:
    global _tables_ready
    if not _tables_ready:
        # 启动时 create_all 可能已建过；这里兜底，避免未跑迁移的机器上 500
        WecomSheetSource.__table__.create(bind=engine, checkfirst=True)
        ExternalRecord.__table__.create(bind=engine, checkfirst=True)
        _tables_ready = True
    return AsyncSessionLocal()


def _dict(row: WecomSheetSource, record_count: int = 0) -> Dict[str, Any]:
    return {
        "id": int(row.id),
        "key": row.key,
        "display_name": row.display_name,
        "docid": row.docid,
        "sheet_id": row.sheet_id,
        "enabled": bool(row.enabled),
        "target_mode": row.target_mode or "mirror",
        "handler_key": row.handler_key,
        "sync_interval_min": int(row.sync_interval_min or 30),
        "notes": row.notes,
        "record_count": record_count,
        "last_sync_at": _fmt_dt(row.last_sync_at),
        "last_stats": row.last_stats,
        "last_error": row.last_error,
        "created_at": _fmt_dt(row.created_at),
        "updated_at": _fmt_dt(row.updated_at),
    }


class SheetSourceCreate(BaseModel):
    key: str = Field(..., description="唯一标识，小写字母数字下划线，如 usp_projects")
    display_name: str = Field(..., min_length=1, max_length=128)
    docid: str = Field(..., min_length=1, max_length=128)
    sheet_id: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True
    target_mode: str = Field("mirror", description="mirror | handler")
    handler_key: Optional[str] = Field(None, max_length=64)
    sync_interval_min: int = Field(30, ge=1, le=1440)
    notes: Optional[str] = None


class SheetSourceUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    docid: Optional[str] = Field(None, min_length=1, max_length=128)
    sheet_id: Optional[str] = Field(None, min_length=1, max_length=128)
    enabled: Optional[bool] = None
    target_mode: Optional[str] = None
    handler_key: Optional[str] = Field(None, max_length=64)
    sync_interval_min: Optional[int] = Field(None, ge=1, le=1440)
    notes: Optional[str] = None


class PreviewRequest(BaseModel):
    docid: str = Field(..., min_length=1, max_length=128)
    sheet_id: str = Field(..., min_length=1, max_length=128)
    limit: int = Field(3, ge=1, le=20)


class CreateDocRequest(BaseModel):
    """新建企微智能表格。

    docid 只在创建时返回一次，所以建表与「登记数据源」必须是一个连续动作：
    页面拿到返回值后立刻填进表单，再由用户保存落库。
    """
    doc_name: str = Field(..., min_length=1, max_length=255)
    spaceid: Optional[str] = Field("", max_length=128, description="空间 spaceid，留空建到默认位置")
    fatherid: Optional[str] = Field("", max_length=128, description="父目录 fileid；根目录填 spaceid")
    admin_users: List[str] = Field(default_factory=list, description="文档管理员 userid")


def _norm_key(key: str) -> str:
    k = (key or "").strip()
    if not _KEY_RE.match(k):
        raise HTTPException(
            status_code=422,
            detail="key 须为 3-64 位小写字母/数字/下划线，且以字母开头",
        )
    return k


def _norm_docid(docid: str) -> str:
    """挡住最常见的坑：把浏览器链接里的 URL ID 当成 docid 填进来。

    链接路径段 s3_/w3_ 不是 docid，同步时必然 301085。这里提前拦下来，
    比等同步失败再排查省事得多。
    """
    d = (docid or "").strip()
    if d.startswith(("s3_", "w3_", "p3_")):
        raise HTTPException(
            status_code=422,
            detail="这是浏览器链接路径段的 URL ID（s3_/w3_ 前缀），不是 docid；"
                   "docid 形如 dc-xxx，请点「新建智能表格」创建，或填已由接口创建的文档 ID",
        )
    return d


def _norm_mode(mode: Optional[str]) -> Optional[str]:
    if mode is None:
        return None
    m = (mode or "").strip().lower()
    if m not in _TARGET_MODES:
        raise HTTPException(status_code=422, detail=f"target_mode 须为 {' 或 '.join(_TARGET_MODES)}")
    return m


async def _get_source(db: AsyncSession, source_id: int) -> WecomSheetSource:
    row = await db.get(WecomSheetSource, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return row


# ── 管理接口 ────────────────────────────────────────────────

@admin_router.get("", response_model=DataResponse, summary="数据源列表")
async def list_sources(current_user: Dict[str, Any] = require_permission(PERM)):
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        rows = (await db.execute(
            select(WecomSheetSource).order_by(WecomSheetSource.id.desc()).limit(200)
        )).scalars().all()
        keys = [r.key for r in rows]
        counts: Dict[str, int] = {}
        if keys:
            pairs = (await db.execute(
                select(ExternalRecord.source_key, func.count(ExternalRecord.id))
                .where(ExternalRecord.source_key.in_(keys))
                .group_by(ExternalRecord.source_key)
            )).all()
            counts = {k: int(c) for k, c in pairs}
        data = [_dict(r, counts.get(r.key, 0)) for r in rows]
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.post("", response_model=DataResponse, summary="新建数据源")
async def create_source(body: SheetSourceCreate,
                        current_user: Dict[str, Any] = require_permission(PERM)):
    ensure_wecom_sheets_permission()
    _ = current_user
    key = _norm_key(body.key)
    mode = _norm_mode(body.target_mode) or "mirror"
    now = _now()
    db = await _open()
    try:
        exists = (await db.execute(
            select(WecomSheetSource.id).where(WecomSheetSource.key == key)
        )).first()
        if exists:
            raise HTTPException(status_code=409, detail=f"key 已存在: {key}")
        row = WecomSheetSource(
            key=key,
            display_name=body.display_name.strip(),
            docid=_norm_docid(body.docid),
            sheet_id=body.sheet_id.strip(),
            enabled=bool(body.enabled),
            target_mode=mode,
            handler_key=(body.handler_key or "").strip() or None,
            sync_interval_min=int(body.sync_interval_min),
            notes=body.notes,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError as e:
            await db.rollback()
            raise HTTPException(status_code=409, detail=f"key 已存在: {key}") from e
        await db.refresh(row)
        data = _dict(row)
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.post("/preview", response_model=DataResponse, summary="测试连接（不落库）")
async def preview_sheet(body: PreviewRequest,
                        current_user: Dict[str, Any] = require_permission(PERM)):
    """拉少量记录，返回真实列名 + 样例行，供新增向导做字段映射。"""
    ensure_wecom_sheets_permission()
    _ = current_user
    client = WecomSheetClient()
    try:
        data = await client.sample(body.docid.strip(), body.sheet_id.strip(), body.limit)
    except WecomSheetClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return DataResponse(code=0, message="success", data=data)


# 注意：带路径参数（/{source_id}）的路由必须排在这些固定子路径之后，否则会被抢先匹配

@admin_router.post("/create-doc", response_model=DataResponse, summary="新建企微智能表格")
async def create_doc(body: CreateDocRequest,
                     current_user: Dict[str, Any] = require_permission(PERM)):
    """调企微 create_doc 建一张智能表格，返回 docid / url / 子表列表。

    这是拿到 API docid 的唯一途径：手工在企微里建的表格，链接里的 s3_xxx
    是 URL ID 不是 docid，直接拿去同步会 301085。docid 只在创建时返回一次，
    页面拿到后必须立刻填进表单并保存。
    """
    ensure_wecom_sheets_permission()
    _ = current_user
    client = WecomSheetClient()
    try:
        data = await client.create_doc(
            doc_name=body.doc_name.strip(),
            spaceid=(body.spaceid or "").strip(),
            fatherid=(body.fatherid or "").strip(),
            admin_users=[u.strip() for u in (body.admin_users or []) if u and u.strip()],
        )
    except WecomSheetClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not data.get("docid"):
        raise HTTPException(status_code=502, detail="AI 服务未返回 docid")
    return DataResponse(code=0, message="success", data=data)


@admin_router.get("/doc-sheets", response_model=DataResponse, summary="查询文档下的子表")
async def doc_sheets(docid: str = Query(..., min_length=1, max_length=128),
                     current_user: Dict[str, Any] = require_permission(PERM)):
    """给用户选「同步哪个子表」，避免从浏览器 URL 上抠 tab 参数。"""
    ensure_wecom_sheets_permission()
    _ = current_user
    client = WecomSheetClient()
    try:
        sheets = await client.list_sheets(docid.strip())
    except WecomSheetClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return DataResponse(code=0, message="success", data={"docid": docid, "sheets": sheets})


@admin_router.get("/{source_id}", response_model=DataResponse, summary="数据源详情")
async def get_source(source_id: int, current_user: Dict[str, Any] = require_permission(PERM)):
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        row = await _get_source(db, source_id)
        count = (await db.execute(
            select(func.count(ExternalRecord.id)).where(ExternalRecord.source_key == row.key)
        )).scalar() or 0
        data = _dict(row, int(count))
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.put("/{source_id}", response_model=DataResponse, summary="更新数据源")
async def update_source(source_id: int, body: SheetSourceUpdate,
                        current_user: Dict[str, Any] = require_permission(PERM)):
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        row = await _get_source(db, source_id)
        payload = body.model_dump(exclude_unset=True)
        if "display_name" in payload and payload["display_name"]:
            row.display_name = payload["display_name"].strip()
        if "docid" in payload and payload["docid"]:
            row.docid = _norm_docid(payload["docid"])
        if "sheet_id" in payload and payload["sheet_id"]:
            row.sheet_id = payload["sheet_id"].strip()
        if "target_mode" in payload:
            row.target_mode = _norm_mode(payload["target_mode"]) or row.target_mode
        if "handler_key" in payload:
            row.handler_key = (payload["handler_key"] or "").strip() or None
        if "notes" in payload:
            row.notes = payload["notes"]
        for key in ("enabled", "sync_interval_min"):
            if key in payload and payload[key] is not None:
                setattr(row, key, payload[key])
        row.updated_at = _now()
        await db.commit()
        await db.refresh(row)
        data = _dict(row)
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.delete("/{source_id}", response_model=DataResponse, summary="删除数据源")
async def delete_source(source_id: int,
                        current_user: Dict[str, Any] = require_permission(PERM)):
    """连同源下的镜像行一起删（镜像只是缓存，删了下次同步会重建）。"""
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        row = await _get_source(db, source_id)
        source_key = row.key
        await db.delete(row)
        await db.execute(
            ExternalRecord.__table__.delete().where(ExternalRecord.source_key == source_key)
        )
        await db.commit()
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data={"id": source_id})


@admin_router.post("/{source_id}/sync", response_model=DataResponse, summary="立即同步一次")
async def sync_source(source_id: int,
                      current_user: Dict[str, Any] = require_permission(PERM)):
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        row = await _get_source(db, source_id)
        source_key = row.key
    finally:
        await db.close()

    try:
        stats = await run_sync(source_key)
    except WecomSheetClientError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return DataResponse(code=0, message="success", data=stats)


@admin_router.get("/{source_id}/records", response_model=DataResponse, summary="查看镜像数据")
async def list_records(
    source_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    q: str = Query("", description="在 values 里做包含匹配"),
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """M1 的可观测入口：确认接进来的数据长什么样。

    q 走 Python 侧匹配（JSON 列没法高效 LIKE），因此先按上限取行再过滤。
    """
    ensure_wecom_sheets_permission()
    _ = current_user
    db = await _open()
    try:
        row = await _get_source(db, source_id)
        rows = (await db.execute(
            select(ExternalRecord)
            .where(ExternalRecord.source_key == row.key)
            .order_by(ExternalRecord.id.asc())
            .limit(1000)
        )).scalars().all()

        items = [{
            "record_id": r.record_id,
            "values": r.values,
            "pulled_at": _fmt_dt(r.pulled_at),
            "changed_at": _fmt_dt(r.changed_at),
        } for r in rows]

        keyword = (q or "").strip()
        if keyword:
            items = [i for i in items if keyword in str(i["values"])]
        total = len(items)
        page = items[skip:skip + limit]
    finally:
        await db.close()
    return DataResponse(code=0, message="success", data={"total": total, "items": page})


# ── 对外接口（X-API-Key，供 Airflow 定时触发）──────────────

@public_router.post("/sync-all", summary="同步全部已启用数据源")
async def sync_all(_: str = Depends(verify_sync_api_key)):
    """一次调用同步所有 enabled 数据源；单源失败不影响其他源。"""
    results = await run_sync_all()
    ok = all(r.get("ok") for r in results)
    return {
        "code": 200 if ok else 207,
        "message": "ok" if ok else "部分数据源同步失败",
        "data": results,
    }
