from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.modules.admin.services import knowledge_challenge as service


router = APIRouter(prefix="/knowledge/challenges", tags=["高价值问题"])


class CreateChallenge(BaseModel):
    request_key: str = Field(min_length=1, max_length=64)
    task_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=20000)
    bounty_fen: int = Field(gt=0, le=2147483647, strict=True)
    ends_at: datetime

    @field_validator("title", "content", "request_key")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写问题标题、内容及发布标识")
        return value.strip()


class Version(BaseModel):
    version: int = Field(ge=1)


class AnswerBody(BaseModel):
    content: str = Field(max_length=20000)
    version: int = Field(ge=0)


class StateBody(Version):
    action: Literal["pause", "resume", "cancel", "extend", "hide", "unhide"]
    reason: str = Field(min_length=1, max_length=2000)
    ends_at: datetime | None = None

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写操作原因")
        return value.strip()


class OverrideBody(Version):
    action: Literal["reject", "replace", "restore"]
    reason: str = Field(min_length=1, max_length=2000)
    answer_id: int | None = Field(default=None, gt=0)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请填写结果调整原因")
        return value.strip()

    @model_validator(mode="after")
    def replacement_required(self):
        if self.action == "replace" and self.answer_id is None:
            raise ValueError("请选择新的最佳回答")
        return self


@router.get("/overview")
async def overview(db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.listing(db, actor, size=3)


@router.get("/source-tasks/{task_id}")
async def source_task(task_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.source_task(db, actor, task_id)


@router.get("")
async def listing(view: Literal["active", "mine", "ended", "manage"] = "active",
                  page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=50),
                  db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.listing(db, actor, view, page, size)


@router.post("", status_code=201)
async def create(body: CreateChallenge, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.create(db, actor, body)


@router.get("/{challenge_id}")
async def detail(challenge_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.detail(db, actor, challenge_id)


@router.get("/{challenge_id}/result")
async def result(challenge_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.community_result(db, actor, challenge_id)


@router.post("/{challenge_id}/publish")
async def publish(challenge_id: int, body: Version, db=Depends(get_async_db),
                  actor=Depends(get_current_active_user_from_token)):
    return await service.publish(db, actor, challenge_id, body.version)


@router.get("/{challenge_id}/my-answer")
async def my_answer(challenge_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.my_answer(db, actor, challenge_id)


@router.put("/{challenge_id}/my-answer")
async def save_answer(challenge_id: int, body: AnswerBody, db=Depends(get_async_db),
                      actor=Depends(get_current_active_user_from_token)):
    return await service.write_answer(db, actor, challenge_id, body.content, body.version)


@router.post("/{challenge_id}/my-answer/submit")
async def submit_answer(challenge_id: int, body: AnswerBody, db=Depends(get_async_db),
                        actor=Depends(get_current_active_user_from_token)):
    return await service.write_answer(db, actor, challenge_id, body.content, body.version, submit=True)


@router.put("/{challenge_id}/like")
async def like(challenge_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, challenge_id, True)


@router.delete("/{challenge_id}/like")
async def unlike(challenge_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, challenge_id, False)


@router.put("/{challenge_id}/answers/{answer_id}/like")
async def like_answer(challenge_id: int, answer_id: int, db=Depends(get_async_db),
                      actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, challenge_id, True, answer_id)


@router.delete("/{challenge_id}/answers/{answer_id}/like")
async def unlike_answer(challenge_id: int, answer_id: int, db=Depends(get_async_db),
                        actor=Depends(get_current_active_user_from_token)):
    return await service.set_like(db, actor, challenge_id, False, answer_id)


@router.patch("/{challenge_id}/state")
async def change_state(challenge_id: int, body: StateBody, db=Depends(get_async_db),
                       actor=Depends(get_current_active_user_from_token)):
    return await service.change_state(db, actor, challenge_id, body)


@router.post("/{challenge_id}/result/override")
async def override(challenge_id: int, body: OverrideBody, db=Depends(get_async_db),
                   actor=Depends(get_current_active_user_from_token)):
    return await service.override(db, actor, challenge_id, body)
