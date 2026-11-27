"""做题人学习中心 API — 挂在 /tasks 下，登录即可访问（无需 PERM_LEARNING）。

路径前缀完整为 /api/tasks/learning/*，前端通过 API_CONFIG.TASKS 调用。
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.modules.tasks.services import learning_learner_service as service
from app.modules.admin.services.learning_content import QuestionContent
from app.services import learning_reviews as reviews

router = APIRouter(prefix="/learning", tags=["tasks-learning"])


class SubmitAnswerBody(BaseModel):
    question_id: str = Field(min_length=1, max_length=32)
    answers: list[str] = Field(min_length=1, max_length=6)


class ReviewDecision(BaseModel):
    revision: int = Field(ge=1)
    action: Literal["approve", "reject"]
    verified: bool = False
    reason: str = Field(default="", max_length=2000)


class ReviewPoint(BaseModel):
    id: str = Field(pattern=r"^KP[1-9][0-9]*$")
    text: str = Field(min_length=1, max_length=2000)


class ReviewArticle(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(max_length=10)
    summary: str = Field(min_length=1, max_length=4000)
    applicability: str = Field(min_length=1, max_length=4000)
    key_points: list[ReviewPoint] = Field(min_length=1, max_length=5)
    method: str = Field(max_length=6000)
    cautions: str = Field(max_length=4000)
    example: str = Field(max_length=4000)


class ReviewEdit(BaseModel):
    revision: int = Field(ge=1)
    article_revision: int = Field(ge=1)
    question: QuestionContent | None = None
    article: ReviewArticle | None = None

    @model_validator(mode="after")
    def has_content(self):
        if self.question is None and self.article is None:
            raise ValueError("请提供需要修改的题目或文章")
        return self


@router.get("/reviews")
async def review_list(page: int = Query(1, ge=1), db=Depends(get_async_db),
                      actor=Depends(get_current_active_user_from_token)):
    return await reviews.listing(db, actor, page)


@router.get("/reviews/{task_id}")
async def review_detail(task_id: int, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await reviews.detail(db, actor, task_id)


@router.post("/reviews/{task_id}/decision")
async def review_decision(task_id: int, body: ReviewDecision, db=Depends(get_async_db),
                          actor=Depends(get_current_active_user_from_token)):
    return await reviews.decide(db, actor, task_id, body)


@router.put("/reviews/{task_id}/content")
async def review_edit(task_id: int, body: ReviewEdit, db=Depends(get_async_db),
                      actor=Depends(get_current_active_user_from_token)):
    return await reviews.edit(db, actor, task_id, body)


@router.get("/question-count")
async def question_count(db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.question_count(db)


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


@router.get("/wrongbook")
async def wrongbook(
    status: str = Query("", max_length=16),
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.list_wrongbook(db, actor, status)


@router.post("/wrongbook/{wrongbook_id}/status")
async def wrongbook_set_status(
    wrongbook_id: str,
    body: "WrongbookStatusBody",
    db=Depends(get_async_db),
    actor=Depends(get_current_active_user_from_token),
):
    return await service.set_wrongbook_status(db, actor, wrongbook_id, body.status)


class WrongbookStatusBody(BaseModel):
    status: Literal["wrong", "mastered", "skipped"]
