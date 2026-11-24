import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.base import Base
from app.models.identity import Permission
from app.models.learning import LearningArticle, LearningArticleVersion, LearningAudit, LearningGeneration
from app.models.task import TaskStatus, TaskType
from app.modules.admin.api.learning import (
    ConfirmArticle, CreateArticle, Generate, QuestionAction, SaveArticle, SaveQuestion, router,
)
from app.modules.admin.api.auth import get_current_active_user_from_token as admin_actor
from app.modules.admin.services import learning_service as service
from app.modules.admin.services.learning_content import ArticleContent, QuestionContent, redact
from app.services.permission_service import PERM_LEARNING
from backend.tests.test_knowledge import actor, db, seed


def administrator():
    return {**actor("reviewer"), "permissions": [PERM_LEARNING]}


@pytest.fixture
def learning_db(db):
    for name in ("learning_articles", "learning_article_versions", "learning_questions", "learning_generations", "learning_audits"):
        Base.metadata.tables[name].create(db.session.get_bind(), checkfirst=True)
    return db


def content():
    return ArticleContent(title="充电故障排查的基本顺序", category="设备维护", summary="检查供电后再检查充电触点。",
        applicability="仅适用于已断电且具备维护资质的人员。",
        key_points=[{"id": "KP1", "text": "检查供电后再检查充电触点。", "source_markers": ["S1"],
            "source_excerpt": "检查供电后再检查充电触点。"}], cautions="不得带电操作。")


def question():
    return {"question_type": "single_choice", "stem": "依据文章，首先检查什么？",
        "options": [{"key": "A", "text": "供电"}, {"key": "B", "text": "充电触点"}], "correct_answer": ["A"],
        "explanation": "文章要求先检查供电。", "point_ids": ["KP1"], "source_excerpt": "检查供电后再检查充电触点。", "difficulty": "medium"}


async def new_article(db):
    return await service.create(db, administrator(), CreateArticle(title="充电故障排查", sources={
        "materials": [{"name": "维护规范", "text": "检查供电后再检查充电触点。不得带电操作。", "provenance": "维护规范v1"}]}))


async def confirmed_article(db):
    article = await new_article(db)
    article = await service.save(db, administrator(), article["id"], SaveArticle(revision=article["revision"], content=content()))
    return await service.confirm(db, administrator(), article["id"], ConfirmArticle(revision=article["revision"], verified=True))


async def generated_question(db, monkeypatch, article):
    client = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({"questions": [question()]})))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    await service.generate(db, administrator(), article["id"], Generate(request_id="question-request-1", revision=article["revision"], kind="questions"))
    return (await service.detail(db, administrator(), article["id"]))["questions"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.CLOSED, TaskStatus.ARCHIVING, TaskStatus.ARCHIVED])
async def test_all_closed_and_later_states_are_allowed(learning_db, status):
    task, _ = seed(learning_db)
    task.status = status
    learning_db.session.commit()
    result = await service.create(learning_db, administrator(), CreateArticle(title="提炼知识", sources={"task_ids": [1]}))
    assert result["sources"][0]["type"] == "work_order"
    assert "秘密项目" not in json.dumps(result["sources"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_source_ids_remain_exact_strings_for_browser_roundtrip(learning_db):
    task_id = 9007199254740993
    task, _ = seed(learning_db, task_id=task_id)
    task.description = f"工单 {task_id}：检查供电"
    learning_db.session.commit()
    article = await service.create(learning_db, administrator(), CreateArticle(title="充电故障", sources={
        "task_ids": [str(task_id)]}))
    assert article["sources"][0]["id"] == str(task_id)
    assert str(task_id) not in article["sources"][0]["content"]
    listed = await service.source_listing(learning_db, administrator(), str(task_id))
    assert listed["items"][0]["id"] == str(task_id)


@pytest.mark.asyncio
async def test_rejects_unclosed_tasks_and_anonymous_review_bypass(learning_db):
    task, report = seed(learning_db)
    task.status = TaskStatus.RESOLVED
    learning_db.session.commit()
    with pytest.raises(HTTPException) as error:
        await service.source_task(learning_db, administrator(), 1)
    assert error.value.status_code == 422
    task.status, report.submit_status = TaskStatus.ARCHIVING, "submitted"
    learning_db.session.commit()
    with pytest.raises(HTTPException) as error:
        await service.source_task(learning_db, administrator(), 1)
    assert error.value.status_code == 403
    assert (await service.source_listing(learning_db, administrator()))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TaskStatus.CLOSED, TaskStatus.ARCHIVING, TaskStatus.ARCHIVED])
@pytest.mark.parametrize("approval_type", ["new_company", "new_department", "new_project", "future_approval"])
async def test_source_listing_excludes_approval_tasks(learning_db, status, approval_type):
    task, _ = seed(learning_db, 1)
    task.title, task.task_type, task.status = "技术审核流程故障", TaskType.OTHER, status
    approval, _ = seed(learning_db, 2)
    approval.title, approval.task_type, approval.status = "新项目录入审核", TaskType.OTHER, status
    approval.metadata_info = {"approval_type": approval_type}
    learning_db.session.commit()
    for keyword in ("", "审核"):
        result = await service.source_listing(learning_db, administrator(), keyword)
        assert result["total"] == 1
        assert [item["id"] for item in result["items"]] == ["1"]
    for keyword in ("2", "录入审核"):
        assert await service.source_listing(learning_db, administrator(), keyword) == {"total": 0, "items": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {}, {"approval_type": None}, {"approval_type": ""}, {"technical_review": True}])
async def test_source_listing_keeps_non_approval_other_tasks(learning_db, metadata):
    task, _ = seed(learning_db)
    task.task_type, task.metadata_info = TaskType.OTHER, metadata
    learning_db.session.commit()
    result = await service.source_listing(learning_db, administrator())
    assert result["total"] == 1
    assert result["items"][0]["id"] == "1"
    article = await service.create(learning_db, administrator(), CreateArticle(title="提炼知识", sources={"task_ids": [1]}))
    assert article["sources"][0]["id"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "save"])
async def test_direct_source_selection_rejects_approval_tasks(learning_db, operation):
    task, _ = seed(learning_db)
    task.metadata_info = {"approval_type": "new_project"}
    learning_db.session.commit()
    article = await new_article(learning_db) if operation == "save" else None
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            await service.create(learning_db, administrator(), CreateArticle(title="提炼知识", sources={"task_ids": [1]}))
        else:
            await service.save(learning_db, administrator(), article["id"], SaveArticle(
                revision=article["revision"], content=content(), sources={"task_ids": [1]}))
    assert error.value.status_code == 422
    assert "审核类工单" in error.value.detail
    if article:
        assert (await service.detail(learning_db, administrator(), article["id"]))["sources"] == article["sources"]


@pytest.mark.asyncio
async def test_similar_sources_exclude_approval_tasks_before_limiting(learning_db, monkeypatch):
    approval_types = {2: "new_company", 3: "new_department", 4: "new_project"}
    for task_id in range(1, 7):
        task, _ = seed(learning_db, task_id)
        if task_id in approval_types:
            task.metadata_info = {"approval_type": approval_types[task_id]}
    learning_db.session.commit()
    fetch = AsyncMock(return_value=([{"task_id": task_id, "score": 0.9} for task_id in (2, 5, 3, 4, 6)], "ok"))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    result = await service.similar_source_tasks(learning_db, administrator(), 1, limit=2)
    assert [item["id"] for item in result["items"]] == ["5", "6"]


@pytest.mark.asyncio
async def test_similar_sources_reject_approval_anchor_without_retrieval(learning_db, monkeypatch):
    task, _ = seed(learning_db)
    task.metadata_info = {"approval_type": "new_company"}
    learning_db.session.commit()
    fetch = AsyncMock(return_value=([], "ok"))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    with pytest.raises(HTTPException) as error:
        await service.similar_source_tasks(learning_db, administrator(), 1)
    assert error.value.status_code == 422
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_rechecks_approval_marker_on_saved_sources(learning_db, monkeypatch):
    task, _ = seed(learning_db)
    article = await service.create(learning_db, administrator(), CreateArticle(title="提炼知识", sources={"task_ids": [1]}))
    task.metadata_info = {"approval_type": "new_project"}
    learning_db.session.commit()
    client = SimpleNamespace(complete=AsyncMock())
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    with pytest.raises(HTTPException) as error:
        await service.generate(learning_db, administrator(), article["id"], Generate(
            request_id="approval-source-check", revision=article["revision"], kind="article"))
    assert error.value.status_code == 422
    client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_previews_are_brief_and_redacted(learning_db):
    task, _ = seed(learning_db)
    task.title = "秘密项目充电桩故障"
    task.description = "秘密项目 提单甲 alice-account 13800138000 secret@example.com token=private-value\n" + "检查充电桩供电。" * 100
    learning_db.session.commit()
    result = await service.source_listing(learning_db, administrator())
    preview = result["items"][0]
    assert preview["id"] == "1"
    assert "充电桩" in preview["summary"]
    assert len(preview["summary"]) <= 241
    assert "\n" not in preview["summary"]
    assert preview["summary"].endswith("…")
    for sensitive in ("秘密项目", "提单甲", "alice-account", "13800138000", "secret@example.com", "private-value"):
        assert sensitive not in json.dumps(preview, ensure_ascii=False)
    assert set(preview) == {"id", "title", "status", "summary"}


@pytest.mark.asyncio
async def test_similar_sources_reuse_vector_order_and_filter_ineligible_tasks(learning_db, monkeypatch):
    for task_id in range(1, 9):
        task, report = seed(learning_db, task_id)
        task.title = "充电桩无法启动"
        task.description = "充电桩断电导致供电中断。秘密项目 secret@example.com"
        if task_id == 2:
            task.title, task.description = "补能模块接触不良", "供电端子松动，重新连接后恢复。"
        elif task_id == 3:
            task.title = "充电桩接触不良"
            task.description = "充电桩供电时触点接触不良"
        elif task_id == 4:
            task.title = "交换机端口丢包"
            task.description = "交换机网络通信中断"
        elif task_id == 5:
            task.status = TaskStatus.RESOLVED
        elif task_id == 6:
            report.submit_status = "submitted"
        elif task_id == 7:
            task.status = TaskStatus.CLOSED
            task.title, task.description = "充电桩供电中断", "充电桩断电"
            learning_db.session.delete(report)
    learning_db.session.commit()
    hits = [
        {"task_id": 5, "score": 0.99}, {"task_id": 6, "score": 0.99}, {"task_id": 999, "score": 0.99},
        {"task_id": 3, "score": 0.55, "summary": "秘密项目 secret@example.com", "reviewer": "private-reviewer"},
        {"task_id": "2", "score": 0.94}, {"task_id": 3, "score": 0.99},
        {"task_id": 1, "score": 0.99}, {"task_id": 8, "score": 0.54},
    ]
    fetch = AsyncMock(return_value=(hits, "ok"))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    result = await service.similar_source_tasks(learning_db, administrator(), 1)
    ids = [item["id"] for item in result["items"]]
    assert ids == ["3", "2"]
    fetch.assert_awaited_once_with(1, 30)
    assert result["source"]["id"] == "1"
    assert result["matching_method"] == "vector"
    assert result["retrieval_status"] == "ok"
    assert all(item["summary"] for item in result["items"])
    assert "秘密项目" not in json.dumps(result, ensure_ascii=False)
    assert "secret@example.com" not in json.dumps(result, ensure_ascii=False)
    assert "private-reviewer" not in json.dumps(result, ensure_ascii=False)
    assert all(set(item) == {"id", "title", "status", "summary"} for item in result["items"])
    assert (await service.similar_source_tasks(learning_db, administrator(), 1, limit=1))["items"][0]["id"] == "3"


@pytest.mark.asyncio
async def test_similar_sources_enforce_anchor_access_and_handle_empty_text(learning_db, monkeypatch):
    fetch = AsyncMock(return_value=([], "ok"))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    task, report = seed(learning_db)
    with pytest.raises(HTTPException) as error:
        await service.similar_source_tasks(learning_db, actor("voter"), 1)
    assert error.value.status_code == 403
    task.status = TaskStatus.IN_PROGRESS
    learning_db.session.commit()
    with pytest.raises(HTTPException) as error:
        await service.similar_source_tasks(learning_db, administrator(), 1)
    assert error.value.status_code == 422
    task.status, report.submit_status = TaskStatus.ARCHIVING, "submitted"
    learning_db.session.commit()
    with pytest.raises(HTTPException) as error:
        await service.similar_source_tasks(learning_db, administrator(), 1)
    assert error.value.status_code == 403
    fetch.assert_not_awaited()
    task.status, report.submit_status = TaskStatus.ARCHIVED, "approved"
    task.title, task.description = "", ""
    learning_db.session.commit()
    result = await service.similar_source_tasks(learning_db, administrator(), 1)
    assert result["items"] == []
    assert result["source"]["summary"] == ""
    fetch.assert_awaited_once_with(1, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval_status", ["ok", "unavailable"])
async def test_similar_sources_do_not_fall_back_to_text_search(learning_db, monkeypatch, retrieval_status):
    seed(learning_db, 1)
    seed(learning_db, 2)
    fetch = AsyncMock(return_value=([], retrieval_status))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    result = await service.similar_source_tasks(learning_db, administrator(), 1)
    assert result["items"] == []
    assert result["retrieval_status"] == retrieval_status
    assert result["matching_method"] == "vector"
    assert (await service.source_listing(learning_db, administrator()))["total"] == 2


@pytest.mark.asyncio
async def test_multiple_tasks_and_manual_only_sources_are_supported(learning_db):
    seed(learning_db, 1)
    seed(learning_db, 2)
    article = await service.create(learning_db, administrator(), CreateArticle(title="共同知识", sources={"task_ids": [1, 2, 1]}))
    assert len(article["sources"]) == 2
    manual = await new_article(learning_db)
    assert manual["sources"][0]["type"] == "manual"
    assert "id" not in manual["sources"][0]


@pytest.mark.asyncio
async def test_redacts_before_model_and_preserves_candidate_until_adopted(learning_db, monkeypatch):
    task, _ = seed(learning_db)
    task.description = "秘密项目 提单甲 alice-account 13800138000 secret@example.com http://192.168.1.1 token=private-value"
    learning_db.session.commit()
    article = await service.create(learning_db, administrator(), CreateArticle(title="秘密项目知识", sources={"task_ids": [1]}))
    client = SimpleNamespace(complete=AsyncMock(return_value=content().model_dump_json()))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    body = Generate(request_id="article-request-1", revision=article["revision"], kind="article")
    job = await service.generate(learning_db, administrator(), article["id"], body)
    assert job["state"] == "succeeded"
    sent = client.complete.call_args.args[0]
    for sensitive in ("秘密项目", "提单甲", "alice-account", "13800138000", "secret@example.com", "192.168.1.1", "private-value"):
        assert sensitive not in sent
    assert "\"id\"" not in sent
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"]["summary"] == ""
    assert (await service.generate(learning_db, administrator(), article["id"], body))["id"] == job["id"]
    assert client.complete.await_count == 1
    adopted = await service.adopt(learning_db, administrator(), article["id"], job["id"], article["revision"])
    assert adopted["draft"]["summary"] == content().summary


@pytest.mark.asyncio
async def test_article_generation_sends_exact_output_schema(learning_db, monkeypatch):
    article = await new_article(learning_db)
    client = SimpleNamespace(complete=AsyncMock(return_value=content().model_dump_json()))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    job = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="article-schema-request", revision=article["revision"], kind="article"))
    assert job["state"] == "succeeded"
    instruction = client.complete.call_args.kwargs["system_prompt"]
    schema = json.loads(instruction.split("\n输出JSON Schema：\n", 1)[1])
    assert schema == ArticleContent.model_json_schema()
    assert schema["properties"]["method"]["type"] == "string"
    assert schema["$defs"]["KnowledgePoint"]["properties"]["source_markers"]["type"] == "array"
    assert "不要返回null" in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_field, invalid_value, expected_path, expected_reason", [
    ("method", ["检查供电", "password=private-ai-value"], "method", "应为文本"),
    ("warnings", None, "warnings", "应为数组"),
    ("summary", "排查" * 2001, "summary", "超过长度上限"),
    ("key_points", [{"id": "KP0", "text": "检查供电", "source_markers": ["S1"],
        "source_excerpt": "检查供电"}], "key_points.0.id", "格式不符合要求"),
    ("key_points", [{"id": "KP1", "text": "检查供电", "source_markers": "S1",
        "source_excerpt": "检查供电"}], "key_points.0.source_markers", "应为数组"),
], ids=["text-array", "null-array", "too-long", "point-id", "source-marker-type"])
async def test_article_validation_reports_safe_field_details_and_preserves_draft(
        learning_db, monkeypatch, caplog, invalid_field, invalid_value, expected_path, expected_reason):
    article = await new_article(learning_db)
    payload = {**content().model_dump(), invalid_field: invalid_value}
    client = SimpleNamespace(complete=AsyncMock(return_value=json.dumps(payload, ensure_ascii=False)))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    job = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="invalid-fields-request", revision=article["revision"], kind="article"))
    assert job["state"] == "failed"
    assert expected_path in job["error"]
    assert expected_reason in job["error"]
    assert len(job["error"]) <= 255
    assert "private-ai-value" not in json.dumps(job) + caplog.text
    assert "检查供电" not in caplog.text
    assert expected_path in caplog.text
    assert client.complete.await_count == 1
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"] == article["draft"]
    client.complete.return_value = content().model_dump_json()
    recovered = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="valid-fields-request", revision=article["revision"], kind="article"))
    assert recovered["state"] == "succeeded"
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"] == article["draft"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected_path, expected_reason", [
    ({"content": content().model_dump()}, "title", "缺少必填字段"),
    ([content().model_dump()], "整体结构", "应为JSON对象"),
], ids=["wrapped-object", "root-array"])
async def test_article_validation_explains_missing_fields_and_wrong_root(
        learning_db, monkeypatch, payload, expected_path, expected_reason):
    article = await new_article(learning_db)
    client = SimpleNamespace(complete=AsyncMock(return_value=json.dumps(payload, ensure_ascii=False)))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    job = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="invalid-root-request", revision=article["revision"], kind="article"))
    assert job["state"] == "failed"
    assert expected_path in job["error"]
    assert expected_reason in job["error"]
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"] == article["draft"]


@pytest.mark.asyncio
async def test_generation_failure_preserves_draft_and_hides_upstream_error(learning_db, monkeypatch):
    article = await new_article(learning_db)
    client = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("token=super-secret 192.168.1.1")))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    job = await service.generate(learning_db, administrator(), article["id"], Generate(request_id="failed-request-1", revision=article["revision"], kind="article"))
    assert job["state"] == "failed"
    assert "super-secret" not in json.dumps(job)
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"] == article["draft"]


@pytest.mark.asyncio
async def test_generation_rechecks_new_identity_terms_before_sending_saved_sources(learning_db, monkeypatch):
    article = await service.create(learning_db, administrator(), CreateArticle(title="充电故障", sources={
        "materials": [{"name": "维护规范", "provenance": "v1", "text": "新识别身份：检查供电后再检查充电触点。"}]}))
    assert "新识别身份" in article["sources"][0]["content"]
    monkeypatch.setattr(service, "known_terms", AsyncMock(return_value=["新识别身份", "S1"]))
    client = SimpleNamespace(complete=AsyncMock(return_value=content().model_dump_json()))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="redact-saved-source-1", revision=article["revision"], kind="article"))
    inputs = json.loads(client.complete.call_args.args[0])
    assert "新识别身份" not in json.dumps(inputs, ensure_ascii=False)
    assert inputs["materials"][0]["marker"] == "S1"


@pytest.mark.asyncio
async def test_immutable_versions_invalidate_old_questions(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    item = await generated_question(learning_db, monkeypatch, article)
    for action in ("submit", "approve", "enable"):
        item = await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=item["revision"], action=action, verified=True))
    assert item["enabled"]
    forked = await service.fork(learning_db, administrator(), article["id"], article["revision"])
    changed = content().model_copy(update={"title": "充电排查新版"})
    saved = await service.save(learning_db, administrator(), article["id"], SaveArticle(revision=forked["revision"], content=changed))
    updated = await service.confirm(learning_db, administrator(), article["id"], ConfirmArticle(revision=saved["revision"], verified=True))
    assert updated["active_version"] == 2
    assert updated["questions"][0]["review_status"] == "draft"
    assert not updated["questions"][0]["enabled"]
    assert updated["versions"][1]["content"]["title"] == content().title
    assert updated["questions"][0]["article_version"] == 1
    with pytest.raises(HTTPException):
        await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=updated["questions"][0]["revision"], action="submit"))


@pytest.mark.asyncio
async def test_review_editing_and_preview_use_correct_bound_content(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    item = await generated_question(learning_db, monkeypatch, article)
    with pytest.raises(HTTPException):
        await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=1, action="enable"))
    item = await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=item["revision"], action="submit"))
    with pytest.raises(HTTPException):
        await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=item["revision"], action="reject"))
    item = await service.question_action(learning_db, administrator(), item["id"], QuestionAction(revision=item["revision"], action="approve", verified=True))
    preview = await service.preview(learning_db, administrator(), article["id"])
    rendered = json.dumps(preview, ensure_ascii=False)
    for private in ("correct_answer", "explanation", "source_markers", "source_excerpt", "sources", "confirmed_by"):
        assert private not in rendered
    answer = await service.preview_answer(learning_db, administrator(), article["id"], item["id"], ["A"])
    assert answer["is_correct"]
    assert answer["correct_answer"] == ["A"]
    edited = QuestionContent.model_validate({**question(), "stem": "根据文章，哪项需要先检查？"})
    changed = await service.save_question(learning_db, administrator(), item["id"], SaveQuestion(revision=item["revision"], content=edited))
    assert changed["review_status"] == "draft" and not changed["enabled"]
    record = learning_db.session.scalar(select(LearningAudit).where(LearningAudit.action == "question.approve"))
    assert record.snapshot["payload"]["stem"] == question()["stem"]


@pytest.mark.asyncio
async def test_confirmation_rejects_missing_basis_warnings_and_stale_revisions(learning_db):
    article = await new_article(learning_db)
    with pytest.raises(HTTPException):
        await service.confirm(learning_db, administrator(), article["id"], ConfirmArticle(revision=article["revision"], verified=True))
    saved = await service.save(learning_db, administrator(), article["id"], SaveArticle(revision=article["revision"], content=content()))
    with pytest.raises(HTTPException) as error:
        await service.save(learning_db, administrator(), article["id"], SaveArticle(revision=article["revision"], content=content()))
    assert error.value.status_code == 409
    await learning_db.rollback()
    bad = content().model_copy(update={"warnings": ["原因尚待核验"]})
    saved = await service.save(learning_db, administrator(), article["id"], SaveArticle(revision=saved["revision"], content=bad))
    with pytest.raises(HTTPException):
        await service.confirm(learning_db, administrator(), article["id"], ConfirmArticle(revision=saved["revision"], verified=True))


def test_question_schema_rejects_ambiguous_and_invalid_answers():
    for update in ({"correct_answer": ["Z"]}, {"correct_answer": ["A", "B"]},
                   {"question_type": "multiple_choice", "correct_answer": ["A"]},
                   {"options": [{"key": "A", "text": "相同"}, {"key": "B", "text": " 相同 "}]}):
        with pytest.raises(ValidationError):
            QuestionContent.model_validate({**question(), **update})
    assert "private-value" not in redact("password=private-value")


@pytest.mark.asyncio
async def test_partial_generation_keeps_valid_drafts_and_reports_rejected_items(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    client = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({"questions": [question(),
        {**question(), "correct_answer": ["Z"]}], "reason": "只有一道题有明确依据"})))
    monkeypatch.setattr(service, "get_llm_client", lambda: client)
    job = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="partial-questions-1", revision=article["revision"], kind="questions", count=2))
    assert job["state"] == "succeeded" and job["result"]["saved_count"] == 1
    assert "第2题" in job["result"]["errors"][0]
    items = (await service.detail(learning_db, administrator(), article["id"]))["questions"]
    assert len(items) == 1 and items[0]["review_status"] == "draft" and not items[0]["enabled"]


@pytest.mark.asyncio
async def test_available_filter_requires_review_enabled_article_and_current_version(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    item = await generated_question(learning_db, monkeypatch, article)
    assert (await service.question_listing(learning_db, administrator(), state="enabled"))["total"] == 0
    for action in ("submit", "approve", "enable"):
        item = await service.question_action(learning_db, administrator(), item["id"], QuestionAction(
            revision=item["revision"], action=action, verified=True))
    assert (await service.question_listing(learning_db, administrator(), state="enabled"))["total"] == 1
    assert len((await service.preview(learning_db, administrator(), article["id"], True))["questions"]) == 1
    await service.toggle_article(learning_db, administrator(), article["id"], article["revision"], False)
    assert (await service.question_listing(learning_db, administrator(), state="enabled"))["total"] == 0
    with pytest.raises(HTTPException):
        await service.preview(learning_db, administrator(), article["id"], True)


@pytest.mark.asyncio
async def test_multiple_choice_requires_exact_answer_set_and_does_not_store_submissions(learning_db, monkeypatch):
    article = await confirmed_article(learning_db)
    payload = {**question(), "question_type": "multiple_choice", "correct_answer": ["A", "B"]}
    monkeypatch.setattr(service, "get_llm_client", lambda: SimpleNamespace(complete=AsyncMock(
        return_value=json.dumps({"questions": [payload]}))))
    await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="multiple-choice-1", revision=article["revision"], kind="questions"))
    item = (await service.detail(learning_db, administrator(), article["id"]))["questions"][0]
    assert not (await service.preview_answer(learning_db, administrator(), article["id"], item["id"], ["A"]))["is_correct"]
    assert (await service.preview_answer(learning_db, administrator(), article["id"], item["id"], ["B", "A"]))["is_correct"]
    with pytest.raises(HTTPException):
        await service.preview_answer(learning_db, administrator(), article["id"], item["id"], ["A", "A"])
    assert not learning_db.session.scalar(select(LearningAudit).where(LearningAudit.action == "preview.answer"))


@pytest.mark.asyncio
async def test_stale_candidate_never_overwrites_saved_changes(learning_db, monkeypatch):
    article = await new_article(learning_db)
    monkeypatch.setattr(service, "get_llm_client", lambda: SimpleNamespace(complete=AsyncMock(return_value=content().model_dump_json())))
    job = await service.generate(learning_db, administrator(), article["id"], Generate(
        request_id="stale-candidate-1", revision=article["revision"], kind="article"))
    changed = content().model_copy(update={"title": "人工保存的修改"})
    saved = await service.save(learning_db, administrator(), article["id"], SaveArticle(revision=article["revision"], content=changed))
    with pytest.raises(HTTPException) as error:
        await service.adopt(learning_db, administrator(), article["id"], job["id"], saved["revision"])
    assert error.value.status_code == 409
    assert (await service.detail(learning_db, administrator(), article["id"]))["draft"]["title"] == "人工保存的修改"


def test_api_denies_unprivileged_direct_requests(learning_db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor("voter")
    app.dependency_overrides[admin_actor] = lambda: actor("voter")
    async def dependency():
        yield learning_db
    app.dependency_overrides[get_async_db] = dependency
    with TestClient(app) as client:
        assert client.get("/learning/articles").status_code == 403
        assert client.get("/learning/sources").status_code == 403
        assert client.get("/learning/sources/1/similar").status_code == 403
    app.dependency_overrides.clear()


def test_source_recommendation_http_contract_and_limits(learning_db, monkeypatch):
    task_id = 9007199254740993
    seed(learning_db, task_id)
    seed(learning_db, 2)
    fetch = AsyncMock(return_value=([{"task_id": 2, "score": 0.72}], "ok"))
    monkeypatch.setattr(service.similar_tickets, "fetch_similar_ticket_hits", fetch)
    app = FastAPI()
    app.include_router(router, prefix="/api/admin")
    app.dependency_overrides[get_current_active_user_from_token] = administrator
    app.dependency_overrides[admin_actor] = administrator
    async def dependency():
        yield learning_db
    app.dependency_overrides[get_async_db] = dependency
    with TestClient(app) as client:
        response = client.get(f"/api/admin/learning/sources/{task_id}/similar")
        assert response.status_code == 200
        assert response.json()["source"]["id"] == str(task_id)
        assert response.json()["items"][0]["id"] == "2"
        assert "summary" in response.json()["items"][0]
        assert response.json()["matching_method"] == "vector"
        assert response.json()["retrieval_status"] == "ok"
        fetch.assert_awaited_once_with(task_id, 30)
        assert client.get(f"/api/admin/learning/sources/{task_id}/similar?limit=11").status_code == 422
        assert client.get(f"/api/admin/learning/sources/{task_id}/similar?limit=0").status_code == 422
    app.dependency_overrides.clear()


def test_admin_http_flow_creates_confirms_generates_and_previews(learning_db, monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/admin")
    app.dependency_overrides[get_current_active_user_from_token] = administrator
    app.dependency_overrides[admin_actor] = administrator
    async def dependency():
        yield learning_db
    app.dependency_overrides[get_async_db] = dependency
    model = SimpleNamespace(complete=AsyncMock(return_value=json.dumps({"questions": [question()]})))
    monkeypatch.setattr(service, "get_llm_client", lambda: model)
    sources = {"materials": [{"name": "维护规范", "provenance": "维护规范v1", "text": "检查供电后再检查充电触点。"}]}
    with TestClient(app) as client:
        response = client.post("/api/admin/learning/articles", json={"title": "充电故障排查", "sources": sources})
        assert response.status_code == 201
        article = response.json()
        base = f'/api/admin/learning/articles/{article["id"]}'
        saved = client.put(base, json={"revision": article["revision"], "content": content().model_dump(), "sources": sources})
        assert saved.status_code == 200
        assert client.post(base + "/confirm", json={"revision": saved.json()["revision"]}).status_code == 422
        confirmed = client.post(base + "/confirm", json={"revision": saved.json()["revision"], "verified": True})
        assert confirmed.status_code == 200 and confirmed.json()["active_version"] == 1
        job = client.post(base + "/generate", json={"revision": confirmed.json()["revision"],
            "request_id": "http-question-1", "kind": "questions"})
        assert job.status_code == 200 and job.json()["result"]["saved_count"] == 1
        listing = client.get("/api/admin/learning/questions?state=draft")
        assert listing.status_code == 200 and listing.json()["total"] == 1
        available = client.get("/api/admin/learning/questions?state=enabled")
        assert available.status_code == 200 and available.json()["total"] == 0
        preview = client.get(base + "/preview").json()
        assert len(preview["questions"]) == 1
        assert "correct_answer" not in preview["questions"][0]
        assert "sources" not in preview["article"]
        assert model.complete.await_count == 1
    app.dependency_overrides.clear()


def test_migration_creates_five_tables_and_permission_idempotently(learning_db):
    path = Path(__file__).parents[1] / "alembic/versions/b5d9f307ca12_learning_content.py"
    spec = importlib.util.spec_from_file_location("learning_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    bind = learning_db.session.get_bind()
    Base.metadata.tables["permissions"].create(bind)
    for name in ("learning_audits", "learning_generations", "learning_questions", "learning_article_versions", "learning_articles"):
        Base.metadata.tables[name].drop(bind)
    with bind.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert "learning_articles" in inspect(connection).get_table_names()
            permissions = connection.execute(select(Permission.code).where(Permission.code == PERM_LEARNING)).scalars().all()
            assert permissions == [PERM_LEARNING]
            migration.downgrade()
            assert "learning_articles" not in inspect(connection).get_table_names()
            assert not connection.execute(select(Permission.code).where(Permission.code == PERM_LEARNING)).first()
