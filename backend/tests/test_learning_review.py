from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.learning import LearningAudit, LearningQuestion
from app.models.organization import Department
from app.models.task import Task, TaskStatus
from app.modules.admin.api.learning import ConfirmArticle, QuestionAction, SaveArticle, SaveQuestion, router as admin_router
from app.modules.admin.api.auth import get_current_active_user_from_token as admin_actor
from app.modules.admin.services import learning_service as service
from app.modules.tasks.api.learning import ReviewDecision, router
from app.services import learning_reviews as reviews
from app.modules.tasks.services.ticket_service import TicketService
from backend.tests.test_knowledge import actor, db
from backend.tests.test_learning import administrator, confirmed_article, content, generated_question, learning_db, question


async def submit(db, monkeypatch, reviewer_id="alice"):
    article = await confirmed_article(db)
    item = await generated_question(db, monkeypatch, article)
    pending = await service.question_action(db, administrator(), item["id"],
        QuestionAction(revision=item["revision"], action="submit", reviewer_id=reviewer_id))
    return article, pending, await db.get(Task, int(pending["review_task_id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewer_id", ["alice", None])
async def test_submit_creates_real_review_task_with_optional_assignee_and_no_duplicates(learning_db, monkeypatch, reviewer_id):
    article, pending, task = await submit(learning_db, monkeypatch, reviewer_id)
    assert task.source == reviews.SOURCE
    assert task.assigned_to == reviewer_id
    assert task.status == (TaskStatus.IN_PROGRESS if reviewer_id else TaskStatus.NEW)
    assert task.created_by == "reviewer"
    assert task.metadata_info["question_revision"] == pending["revision"]
    assert task.metadata_info["article_id"] == article["id"]
    assert "correct_answer" not in str(task.metadata_info)
    assert pending["review_status"] == "pending_review" and not pending["enabled"]
    reviews.notify_review.assert_awaited_once()
    with pytest.raises(HTTPException) as error:
        await service.question_action(learning_db, administrator(), pending["id"],
            QuestionAction(revision=pending["revision"] - 1, action="submit", reviewer_id=reviewer_id))
    assert error.value.status_code == 409
    assert learning_db.session.scalar(select(func.count()).select_from(Task).where(Task.source == reviews.SOURCE)) == 1
    audit = learning_db.session.scalar(select(LearningAudit).where(LearningAudit.action == "question.submit"))
    assert audit.snapshot["review_task_id"] == str(task.id)


@pytest.mark.asyncio
async def test_reviewer_directory_includes_all_active_roles_and_department_names(learning_db):
    department = Department(id="dep-rd", name="研发部", status="approved")
    learning_db.session.add(department)
    user = await learning_db.get(UserDB, "alice")
    user.department_id = department.id
    user.department = "过时部门名"
    (await learning_db.get(UserDB, "bob")).status = "inactive"
    await learning_db.commit()
    items = (await reviews.reviewers(learning_db))["items"]
    assert {item["id"] for item in items} == {"alice", "reviewer", "voter"}
    assert next(item for item in items if item["id"] == "alice")["department"] == "研发部"
    assert all(set(item) == {"id", "username", "name", "department_id", "department"} for item in items)


@pytest.mark.asyncio
@pytest.mark.parametrize("reviewer_id", ["missing", "bob"])
async def test_invalid_reviewer_does_not_change_question_or_create_task(learning_db, monkeypatch, reviewer_id):
    (await learning_db.get(UserDB, "bob")).status = "inactive"
    await learning_db.commit()
    article = await confirmed_article(learning_db)
    item = await generated_question(learning_db, monkeypatch, article)
    with pytest.raises(HTTPException) as error:
        await service.question_action(learning_db, administrator(), item["id"],
            QuestionAction(revision=item["revision"], action="submit", reviewer_id=reviewer_id))
    assert error.value.status_code == 422
    assert (await learning_db.get(LearningQuestion, item["id"])).review_status == "draft"
    assert learning_db.session.scalar(select(func.count()).select_from(Task)) == 0
    reviews.notify_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_assigned_non_admin_can_review_only_own_task_and_close_it(learning_db, monkeypatch):
    article, pending, task = await submit(learning_db, monkeypatch)
    assert (await reviews.listing(learning_db, actor("alice")))["total"] == 1
    assert (await reviews.listing(learning_db, administrator()))["total"] == 1
    assert (await reviews.listing(learning_db, actor("bob")))["total"] == 0
    with pytest.raises(HTTPException) as hidden:
        await reviews.detail(learning_db, actor("bob"), task.id)
    assert hidden.value.status_code == 404
    with pytest.raises(HTTPException) as creator:
        await reviews.decide(learning_db, administrator(), task.id, ReviewDecision(
            revision=pending["revision"], action="approve", verified=True))
    assert creator.value.status_code == 403
    detail = await reviews.detail(learning_db, actor("alice"), task.id)
    assert detail["question"]["correct_answer"] == ["A"]
    assert "sources" not in detail["article"] and "source_markers" not in str(detail["article"])
    assert detail["can_review"]
    with pytest.raises(HTTPException) as unchecked:
        await reviews.decide(learning_db, actor("alice"), task.id,
            ReviewDecision(revision=pending["revision"], action="approve"))
    assert unchecked.value.status_code == 422
    result = await reviews.decide(learning_db, actor("alice"), task.id,
        ReviewDecision(revision=pending["revision"], action="approve", verified=True))
    assert result["status"] == "closed" and result["outcome"] == "approved" and not result["can_review"]
    assert result["question"] == pending["payload"]
    item = await learning_db.get(LearningQuestion, pending["id"])
    assert item.review_status == "approved" and not item.enabled
    audit = learning_db.session.scalar(select(LearningAudit).where(LearningAudit.action == "question.approve"))
    assert audit.operator_id == "alice"
    with pytest.raises(HTTPException) as duplicate:
        await reviews.decide(learning_db, actor("alice"), task.id,
            ReviewDecision(revision=pending["revision"], action="approve", verified=True))
    assert duplicate.value.status_code == 409


@pytest.mark.asyncio
async def test_automatic_assignment_uses_current_task_assignee_and_reject_requires_reason(learning_db, monkeypatch):
    article, pending, task = await submit(learning_db, monkeypatch, None)
    assert not (await reviews.detail(learning_db, administrator(), task.id))["can_review"]
    task.assigned_to = "alice-account"
    task.status = TaskStatus.IN_PROGRESS
    await learning_db.commit()
    assert (await reviews.detail(learning_db, actor("alice"), task.id))["can_review"]
    with pytest.raises(HTTPException) as no_reason:
        await reviews.decide(learning_db, actor("alice"), task.id,
            ReviewDecision(revision=pending["revision"], action="reject"))
    assert no_reason.value.status_code == 422
    result = await reviews.decide(learning_db, actor("alice"), task.id,
        ReviewDecision(revision=pending["revision"], action="reject", reason="请补充选项说明"))
    assert result["status"] == "closed" and result["outcome"] == "rejected"
    assert (await learning_db.get(LearningQuestion, pending["id"])).review_status == "rejected"
    revised = await service.question_action(learning_db, administrator(), pending["id"],
        QuestionAction(revision=pending["revision"] + 1, action="submit", reviewer_id="bob"))
    assert revised["review_task_id"] != str(task.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["question", "article"])
async def test_question_or_article_edit_invalidates_old_review(learning_db, monkeypatch, change):
    article, pending, task = await submit(learning_db, monkeypatch)
    if change == "question":
        await service.save_question(learning_db, administrator(), pending["id"],
            SaveQuestion(revision=pending["revision"], content={**question(), "stem": "首先应检查哪个部位？"}))
    else:
        draft = await service.fork(learning_db, administrator(), article["id"], article["revision"])
        saved = await service.save(learning_db, administrator(), article["id"],
            SaveArticle(revision=draft["revision"], content=content()))
        await service.confirm(learning_db, administrator(), article["id"],
            ConfirmArticle(revision=saved["revision"], verified=True))
    result = await reviews.detail(learning_db, actor("alice"), task.id)
    assert result["status"] == "canceled" and not result["can_review"]
    assert result["question"]["stem"] == pending["payload"]["stem"]
    with pytest.raises(HTTPException) as stale:
        await reviews.decide(learning_db, actor("alice"), task.id,
            ReviewDecision(revision=pending["revision"], action="approve", verified=True))
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_transaction_rolls_back_question_if_task_creation_fails(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    item = await generated_question(learning_db, monkeypatch, article)
    monkeypatch.setattr(reviews, "create_review", AsyncMock(side_effect=RuntimeError("cannot write task")))
    with pytest.raises(RuntimeError):
        await service.question_action(learning_db, administrator(), item["id"],
            QuestionAction(revision=item["revision"], action="submit"))
    await learning_db.rollback()
    restored = await learning_db.get(LearningQuestion, item["id"])
    assert restored.review_status == "draft" and restored.revision == item["revision"]
    reviews.notify_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_failure_does_not_turn_committed_submission_into_failure(learning_db, monkeypatch):
    monkeypatch.setattr(reviews, "notify_review", AsyncMock(side_effect=RuntimeError("offline")))
    article, pending, task = await submit(learning_db, monkeypatch, None)
    assert pending["review_status"] == "pending_review"
    assert (await learning_db.get(Task, task.id)).status == TaskStatus.NEW


@pytest.mark.asyncio
async def test_disabled_article_cannot_be_approved_or_rejected_by_assigned_reviewer(learning_db, monkeypatch):
    article, pending, task = await submit(learning_db, monkeypatch)
    await service.toggle_article(learning_db, administrator(), article["id"], article["revision"], False)
    assert not (await reviews.detail(learning_db, actor("alice"), task.id))["can_review"]
    for action in ("approve", "reject"):
        with pytest.raises(HTTPException) as disabled:
            await reviews.decide(learning_db, actor("alice"), task.id,
                ReviewDecision(revision=pending["revision"], action=action, verified=True, reason="请补充说明"))
        assert disabled.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_decision_closes_task_and_review_task_cannot_become_source(learning_db, monkeypatch):
    article, pending, task = await submit(learning_db, monkeypatch)
    await service.question_action(learning_db, administrator(), pending["id"],
        QuestionAction(revision=pending["revision"], action="approve", verified=True))
    assert (await learning_db.get(Task, task.id)).status == TaskStatus.CLOSED
    assert not (await service.source_listing(learning_db, administrator()))["items"]
    with pytest.raises(HTTPException):
        await service.source_task(learning_db, administrator(), task.id)
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=task))
    with pytest.raises(HTTPException) as direct:
        await TicketService.update_ticket_status(learning_db, task.id, TaskStatus.RESOLVED)
    assert direct.value.status_code == 422
    from app.modules.tasks.schemas.ticket import TicketUpdate

    for patch in ({"status": "closed"}, {"metadata_info": {"question_id": "forged"}}):
        with pytest.raises(HTTPException) as update:
            await TicketService.update_ticket(learning_db, task.id, TicketUpdate(**patch))
        assert update.value.status_code == 422


def test_reviewer_routes_work_without_admin_permission_and_reject_unsupported_actions(learning_db, monkeypatch):
    import asyncio

    article, pending, task = asyncio.run(submit(learning_db, monkeypatch))
    app = FastAPI()
    app.include_router(router, prefix="/tasks")
    app.include_router(admin_router, prefix="/admin")
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor("alice")
    app.dependency_overrides[admin_actor] = lambda: actor("alice")
    app.dependency_overrides[get_async_db] = lambda: learning_db
    with TestClient(app) as client:
        assert client.get("/tasks/learning/reviews").json()["total"] == 1
        assert client.get(f"/tasks/learning/reviews/{task.id}").json()["can_review"]
        assert client.get("/admin/learning/reviewers").status_code == 403
        assert client.post(f"/tasks/learning/reviews/{task.id}/decision", json={
            "revision": pending["revision"], "action": "enable"}).status_code == 422
        result = client.post(f"/tasks/learning/reviews/{task.id}/decision", json={
            "revision": pending["revision"], "action": "approve", "verified": True})
        assert result.status_code == 200 and result.json()["outcome"] == "approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("assigned", [None, "alice"])
async def test_post_commit_notification_reuses_existing_dispatch_or_direct_notification(monkeypatch, assigned):
    from app.modules.tasks.services import ticket_service
    from app.utils.notification_utils import NotificationUtils

    publish = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(ticket_service, "_publish_new_ticket_to_assigner", publish)
    monkeypatch.setattr(NotificationUtils, "send_ticket_create_notification", notify)
    task = Task(id=123, title="题目审核", created_by="reviewer", assigned_to=assigned)
    await reviews.notify_review(task)
    if assigned:
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["user_names"] == ["alice"]
        publish.assert_not_awaited()
    else:
        publish.assert_awaited_once_with(123)
        notify.assert_not_awaited()
