"""公告 API（活动中心数据源）。

后台「公告管理」：列表 / 搜索 / 新建 / 编辑 / 删除 / 上线 / 下线 / 置顶。
前台只读：首页固定公告条取「当前生效的一条」，抽屉内取「已发布公告列表」。
两个 router 同文件定义：
  - `router`（prefix=/announcements，挂在 admin_router 下 → /api/admin/announcements）
  - `frontend_router`（prefix=/announcements，在 app/__init__.py 以 /api/announcements 挂载）

鉴权：
  - 后台：require_permission(PERM_ANNOUNCEMENT)，码定义在 permission_service
  - 前台：仅要求登录（App 全量在 AuthGuard 内，无匿名场景）

Status 用字符串列（draft/published/offline），见 app/models/announcement.py。
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db as get_db
from app.core.user_identity import actor_username
from app.models.announcement import Announcement, AnnouncementStatus
from app.modules.admin.api.auth import require_permission
from app.services.identity_service import IdentityService
from app.services.permission_service import PERM_ANNOUNCEMENT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/announcements", tags=["admin-announcements"])
frontend_router = APIRouter(prefix="/announcements", tags=["announcements"])

# 与 permission_service 的派生表共用同一个常量：全局角色「超级管理员」默认拿到它，
# 其他角色可在权限管理里勾选。两处若各写一份字符串，迟早漂移。
PERM_CODE = PERM_ANNOUNCEMENT

VALID_STATUS = (
    AnnouncementStatus.DRAFT,
    AnnouncementStatus.PUBLISHED,
    AnnouncementStatus.OFFLINE,
)

# 统一排序：置顶优先 → 发布时间倒序 → id 倒序（发布时间为空则靠后）
_ORDER_BY = (
    Announcement.is_pinned.desc(),
    Announcement.published_at.desc(),
    Announcement.id.desc(),
)


def ensure_announcement_permission() -> None:
    """确保权限码已注册到 permissions 表（幂等，已存在自动跳过）。

    只在「权限管理」列表的注册块（permissions.get_all_permissions）里调用：
    该码要在权限管理页可勾选，就必须在**读到权限列表时**先落库。切勿在下面各接口
    函数体内调用——require_permission 是 FastAPI 依赖，先于函数体执行，没权限的
    账号会先被 403 挡住，永远走不到注册那行（生产上就是这样自锁的）。
    """
    IdentityService.add_permission(
        permission_id="perm_frontend_admin_announcement_manage",
        code=PERM_CODE,
        name="公告管理",
        resource_type="frontend",
        action="manage",
        description="后台「公告管理」维护活动中心公告（发布 / 下线 / 置顶）",
    )


class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="公告标题")
    content: str = Field(..., min_length=1, description="公告正文")
    is_pinned: bool = Field(False, description="是否置顶（首页公告条优先展示）")
    status: str = Field(AnnouncementStatus.DRAFT, description="draft/published/offline")


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    is_pinned: Optional[bool] = None
    status: Optional[str] = None


def _validate_status(status: str) -> str:
    if status not in VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"非法状态：{status}")
    return status


def _to_dict(a: Announcement) -> Dict[str, Any]:
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content,
        "is_pinned": bool(a.is_pinned),
        "status": a.status,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "created_by": a.created_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _get_or_404(db: AsyncSession, aid: int) -> Announcement:
    ann = (await db.execute(
        select(Announcement).where(Announcement.id == aid)
    )).scalars().first()
    if ann is None:
        raise HTTPException(status_code=404, detail="公告未找到")
    return ann


# ------------------------------------------------------------------
# 前台只读（登录即可）
# ------------------------------------------------------------------

@frontend_router.get("/active", summary="首页公告条：当前生效的一条公告")
async def get_active_announcement(
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """只常驻展示一条：已发布 → 置顶优先 → 发布时间倒序。无公告时 item 为 null。"""
    stmt = (
        select(Announcement)
        .where(Announcement.status == AnnouncementStatus.PUBLISHED)
        .order_by(*_ORDER_BY)
        .limit(1)
    )
    ann = (await db.execute(stmt)).scalars().first()
    return {"item": _to_dict(ann) if ann else None}


@frontend_router.get("", summary="活动中心：已发布公告列表")
async def list_published_announcements(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    cond = Announcement.status == AnnouncementStatus.PUBLISHED
    total = (await db.execute(
        select(func.count(Announcement.id)).where(cond)
    )).scalar_one()
    stmt = select(Announcement).where(cond).order_by(*_ORDER_BY).offset(skip).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"total": total, "items": [_to_dict(a) for a in rows]}


# ------------------------------------------------------------------
# 后台管理
# ------------------------------------------------------------------

@router.get("", summary="公告列表（后台）")
async def admin_list_announcements(
    status: Optional[str] = Query(None, description="draft/published/offline"),
    keyword: Optional[str] = Query(None, description="标题/正文关键字"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    conds = []
    if status:
        conds.append(Announcement.status == _validate_status(status))
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        conds.append(or_(Announcement.title.like(kw), Announcement.content.like(kw)))

    total_stmt = select(func.count(Announcement.id))
    list_stmt = select(Announcement)
    for c in conds:
        total_stmt = total_stmt.where(c)
        list_stmt = list_stmt.where(c)

    total = (await db.execute(total_stmt)).scalar_one()
    rows = (await db.execute(
        list_stmt.order_by(*_ORDER_BY).offset(skip).limit(limit)
    )).scalars().all()
    return {"total": total, "items": [_to_dict(a) for a in rows]}


@router.post("", summary="新建公告")
async def create_announcement(
    payload: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = require_permission(PERM_CODE),
):
    st = _validate_status(payload.status)
    ann = Announcement(
        title=payload.title.strip(),
        content=payload.content,
        is_pinned=payload.is_pinned,
        status=st,
        published_at=datetime.now() if st == AnnouncementStatus.PUBLISHED else None,
        created_by=actor_username(current_user),
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return {"ok": True, "item": _to_dict(ann)}


@router.put("/{aid}", summary="编辑公告")
async def update_announcement(
    aid: int,
    payload: AnnouncementUpdate,
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ann = await _get_or_404(db, aid)

    if payload.title is not None:
        ann.title = payload.title.strip()
    if payload.content is not None:
        ann.content = payload.content
    if payload.is_pinned is not None:
        ann.is_pinned = payload.is_pinned
    if payload.status is not None:
        st = _validate_status(payload.status)
        if st == AnnouncementStatus.PUBLISHED and ann.published_at is None:
            ann.published_at = datetime.now()
        ann.status = st

    await db.commit()
    await db.refresh(ann)
    return {"ok": True, "item": _to_dict(ann)}


@router.post("/{aid}/publish", summary="上线公告")
async def publish_announcement(
    aid: int,
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ann = await _get_or_404(db, aid)
    ann.status = AnnouncementStatus.PUBLISHED
    if ann.published_at is None:
        ann.published_at = datetime.now()
    await db.commit()
    await db.refresh(ann)
    return {"ok": True, "item": _to_dict(ann)}


@router.post("/{aid}/offline", summary="下线公告")
async def offline_announcement(
    aid: int,
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ann = await _get_or_404(db, aid)
    ann.status = AnnouncementStatus.OFFLINE
    await db.commit()
    await db.refresh(ann)
    return {"ok": True, "item": _to_dict(ann)}


@router.delete("/{aid}", summary="删除公告")
async def delete_announcement(
    aid: int,
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ann = await _get_or_404(db, aid)
    await db.delete(ann)
    await db.commit()
    return {"ok": True, "id": aid}
