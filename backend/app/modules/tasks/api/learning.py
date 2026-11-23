"""做题人学习中心 API — 挂在 /tasks 下，登录即可访问（无需 PERM_LEARNING）。

路径前缀完整为 /api/tasks/learning/*，前端通过 API_CONFIG.TASKS 调用。
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.modules.tasks.services import learning_learner_service as service

router = APIRouter(prefix="/learning", tags=["tasks-learning"])


class SubmitAnswerBody(BaseModel):
    question_id: str = Field(min_length=1, max_length=32)
    answers: list[str] = Field(min_length=1, max_length=6)


@router.get("/articles")
async def articles(
    keyword: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.list_articles(db, actor, keyword, page)


@router.get("/articles/{article_id}")
async def article_detail(
    article_id: str,
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.get_article(db, actor, article_id)


@router.post("/articles/{article_id}/answer")
async def submit_answer(
    article_id: str,
    body: SubmitAnswerBody,
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.submit_answer(db, actor, article_id, body.question_id, body.answers)


@router.get("/progress")
async def progress(
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.get_progress(db, actor)
