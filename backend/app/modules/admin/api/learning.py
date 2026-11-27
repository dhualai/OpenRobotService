from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.modules.admin.api.auth import require_permission
from app.modules.admin.services import learning_service as service
from app.modules.admin.services.learning_content import ArticleContent, QuestionContent
from app.services.permission_service import PERM_LEARNING


router = APIRouter(prefix="/learning", tags=["admin-learning"], dependencies=[require_permission(PERM_LEARNING)])


class Material(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=16000)
    provenance: str = Field(min_length=1, max_length=1000)


class Sources(BaseModel):
    task_ids: list[int] = Field(default_factory=list, max_length=10)
    materials: list[Material] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid_sources(self):
        if any(task_id <= 0 for task_id in self.task_ids):
            raise ValueError("工单ID必须为正整数")
        return self


class CreateArticle(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    sources: Sources


class Revision(BaseModel):
    revision: int = Field(ge=1)


class SaveArticle(Revision):
    content: ArticleContent
    sources: Sources | None = None


class ConfirmArticle(Revision):
    verified: bool = False


class Toggle(Revision):
    enabled: bool


class Generate(Revision):
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    kind: Literal["article", "questions"]
    count: int = Field(default=3, ge=1, le=10)
    question_types: list[Literal["single_choice", "multiple_choice", "true_false"]] = Field(
        default_factory=lambda: ["single_choice", "multiple_choice", "true_false"], min_length=1, max_length=3)
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class SaveQuestion(Revision):
    content: QuestionContent


class QuestionAction(Revision):
    action: Literal["submit", "approve", "reject", "enable", "disable"]
    verified: bool = False
    reason: str = Field(default="", max_length=2000)
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/reviewers")
async def reviewers(db=Depends(get_async_db)):
    from app.services import learning_reviews

    return await learning_reviews.reviewers(db)


class PreviewAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=32)
    answers: list[str] = Field(min_length=1, max_length=6)


@router.get("/sources")
async def sources(keyword: str = Query("", max_length=200), page: int = Query(1, ge=1),
                  db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.source_listing(db, actor, keyword, page)


@router.get("/sources/{task_id}/similar")
async def similar_sources(task_id: int, limit: int = Query(10, ge=1, le=10),
                          db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.similar_source_tasks(db, actor, task_id, limit)


@router.get("/articles")
async def articles(keyword: str = Query("", max_length=200),
                   state: Literal["", "draft", "enabled", "disabled"] = "", page: int = Query(1, ge=1),
                   db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.listing(db, actor, keyword, state, page)


@router.post("/articles", status_code=201)
async def create(body: CreateArticle, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.create(db, actor, body)


@router.get("/articles/{article_id}")
async def detail(article_id: str, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.detail(db, actor, article_id)


@router.put("/articles/{article_id}")
async def save(article_id: str, body: SaveArticle, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.save(db, actor, article_id, body)


@router.post("/articles/{article_id}/fork")
async def fork(article_id: str, body: Revision, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.fork(db, actor, article_id, body.revision)


@router.post("/articles/{article_id}/confirm")
async def confirm(article_id: str, body: ConfirmArticle, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.confirm(db, actor, article_id, body)


@router.post("/articles/{article_id}/toggle")
async def toggle(article_id: str, body: Toggle, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.toggle_article(db, actor, article_id, body.revision, body.enabled)


@router.post("/articles/{article_id}/generate")
async def generate(article_id: str, body: Generate, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.generate(db, actor, article_id, body)


@router.get("/articles/{article_id}/generations/{request_id}")
async def generation(article_id: str, request_id: str, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.generation(db, actor, article_id, request_id)


@router.post("/articles/{article_id}/generations/{request_id}/adopt")
async def adopt(article_id: str, request_id: str, body: Revision, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.adopt(db, actor, article_id, request_id, body.revision)


@router.get("/questions")
async def questions(keyword: str = Query("", max_length=200),
                    state: Literal["", "draft", "pending_review", "approved", "rejected", "stale", "enabled"] = "",
                    page: int = Query(1, ge=1), db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.question_listing(db, actor, keyword, state, page)


@router.put("/questions/{question_id}")
async def save_question(question_id: str, body: SaveQuestion, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.save_question(db, actor, question_id, body)


@router.post("/questions/{question_id}/actions")
async def question_action(question_id: str, body: QuestionAction, db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.question_action(db, actor, question_id, body)


@router.get("/articles/{article_id}/preview")
async def preview(article_id: str, available_only: bool = False,
                  db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.preview(db, actor, article_id, available_only)


@router.post("/articles/{article_id}/preview/answer")
async def preview_answer(article_id: str, body: PreviewAnswer,
                         db=Depends(get_async_db), actor=Depends(get_current_active_user_from_token)):
    return await service.preview_answer(db, actor, article_id, body.question_id, body.answers)
