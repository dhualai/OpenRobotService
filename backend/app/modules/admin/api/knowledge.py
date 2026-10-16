from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.core.user_identity import is_admin_user
from app.modules.admin.api.auth import has_permission_code
from app.modules.admin.services import knowledge_service as service
from app.modules.admin.services import knowledge_settlement as settlement


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def can_manage(actor):
    return is_admin_user(actor) or has_permission_code(actor, "backend:knowledge_incentive:manage")


async def manager(actor=Depends(get_current_active_user_from_token)):
    if not can_manage(actor):
        raise HTTPException(403, "需要知识激励管理权限")
    return actor


class ReviewNote(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写异常核验说明")
        return value.strip()


class RiskMark(ReviewNote):
    status: Literal["valid", "pending", "invalid"]


class SnapshotReview(ReviewNote):
    approved: bool


class DiscussionCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写讨论内容")
        return value.strip()


@router.get("/overview")
async def get_overview(month: str | None = None, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return {**await service.overview(db, actor, month), "can_manage": can_manage(actor)}


@router.get("/rankings")
async def get_rankings(month: str | None = None, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    month = service.selected_month(month)
    await service.actor_id(db, actor)
    return {"month": month, "items": await service.rankings(db, month)}


@router.get("/questions")
async def questions(month: str | None = None, sort: Literal["archived_at", "likes"] = "archived_at",
                    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                    db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    if month:
        service.month_bounds(month)
    return await service.question_items(db, actor, month, sort, page, size)


@router.get("/questions/{task_id}")
async def question(task_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return (await service.question_items(db, actor, task_id=task_id))["items"][0]


@router.get("/contributors/{user_id}/questions")
async def contributor_questions(user_id: str, month: str | None = None,
                                role: Literal["all", "questioner", "answerer"] = "all",
                                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                                db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.contributor_questions(db, actor, user_id, month, role, page, size)


@router.get("/questions/{task_id}/discussions")
async def discussions(task_id: int, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                      db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.discussion_items(db, actor, task_id, page, size)


@router.post("/questions/{task_id}/discussions", status_code=201)
async def create_discussion(task_id: int, body: DiscussionCreate,
                            db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.add_discussion(db, actor, task_id, body.content)


@router.put("/questions/{task_id}/like")
async def like(task_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, task_id, True)


@router.delete("/questions/{task_id}/like")
async def unlike(task_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, task_id, False)


@router.get("/risk")
async def risks(month: str, page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                db=Depends(get_async_db), actor=Depends(manager)):
    return await settlement.risk_items(db, month, page, size)


@router.put("/risk/likes/{task_id}/{user_id}")
async def risk_mark(task_id: int, user_id: str, body: RiskMark, db=Depends(get_async_db), actor=Depends(manager)):
    return await settlement.mark_like_risk(db, actor, task_id, user_id, body.status, body.note)


@router.post("/settlements/{month}/freeze")
async def freeze(month: str, body: ReviewNote, db=Depends(get_async_db), actor=Depends(manager)):
    return await settlement.freeze_month(db, actor, month, body.note)


@router.post("/settlements/{month}/review")
async def review(month: str, body: SnapshotReview, db=Depends(get_async_db), actor=Depends(manager)):
    return await settlement.review_snapshot(db, actor, month, body.approved, body.note)


@router.post("/settlements/{month}/grant")
async def grant(month: str, db=Depends(get_async_db), actor=Depends(manager)):
    service.month_bounds(month)
    return await settlement.grant_month(db, actor, month)
