import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.knowledge_challenge import (
    KnowledgeChallenge, KnowledgeChallengeAnswer, KnowledgeChallengeAudit, KnowledgeChallengeLike,
)
from app.models.task import Task
from app.models.wxpay import RewardRecord
from app.modules.admin.api.knowledge_challenge import CreateChallenge, OverrideBody, StateBody, router
from app.modules.admin.services import knowledge_challenge as service
from app.modules.admin.services import knowledge_service as knowledge
from backend.tests.test_knowledge import actor, db, seed


def admin():
    return {**actor("reviewer"), "is_admin": True}


async def published(db, key="first", task_id=None):
    body = CreateChallenge(request_key=key, task_id=task_id, title="复杂定位问题",
        content="请协助排查长期定位漂移", bounty_fen=10000,
        ends_at=(service.utcnow() + timedelta(days=1)).replace(tzinfo=timezone.utc))
    draft = await service.create(db, admin(), body)
    return await service.publish(db, admin(), draft["id"], draft["version"])


async def submitted(db, challenge_id, user_id="alice", content="先检测电源再检查定位参数"):
    await service.write_answer(db, actor(user_id), challenge_id, content, 0, submit=True)
    return db.session.scalar(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge_id,
        KnowledgeChallengeAnswer.author_id == user_id))


def after_deadline(monkeypatch):
    monkeypatch.setattr(knowledge, "utcnow", lambda: datetime(2026, 11, 22))


@pytest.mark.asyncio
async def test_standalone_full_lifecycle_without_creating_tasks(db, monkeypatch):
    challenge = await published(db)
    assert challenge["source"] == "standalone"
    assert challenge["questioner"] == "平台发布"
    assert not db.session.scalars(select(Task)).all()
    answer = await submitted(db, challenge["id"])
    await service.set_like(db, actor("voter"), challenge["id"], True, answer.id)
    after_deadline(monkeypatch)
    assert await service.settle(db, challenge["id"]) is True
    assert await service.settle(db, challenge["id"]) is False
    result = await service.detail(db, actor("voter"), challenge["id"])
    assert result["best_answer"]["id"] == answer.id
    assert result["professionally_verified"] is False
    assert "task_id" not in result
    reward = db.session.scalar(select(RewardRecord))
    assert (reward.source_type, reward.source_id, reward.user_id, reward.amount, reward.role) == (
        "knowledge_challenge", str(challenge["id"]), "alice", 10000, "answerer")
    assert reward.status == "pending_audit"
    assert len(db.session.scalars(select(RewardRecord)).all()) == 1
    assert not db.session.scalars(select(Task)).all()


@pytest.mark.asyncio
async def test_highest_answer_likes_win_not_question_likes(db, monkeypatch):
    challenge = await published(db)
    first = await submitted(db, challenge["id"], "alice")
    second = await submitted(db, challenge["id"], "bob", "另一种定位方法")
    await service.set_like(db, actor("voter"), challenge["id"], True)
    await service.set_like(db, actor("voter"), challenge["id"], True, second.id)
    after_deadline(monkeypatch)
    await service.settle(db, challenge["id"])
    assert db.session.get(KnowledgeChallenge, challenge["id"]).best_answer_id == second.id
    assert first.id != second.id


@pytest.mark.asyncio
async def test_zero_votes_and_ties_use_first_submission_and_id(db, monkeypatch):
    challenge = await published(db)
    first = await submitted(db, challenge["id"], "alice")
    await submitted(db, challenge["id"], "bob")
    after_deadline(monkeypatch)
    await service.settle(db, challenge["id"])
    assert db.session.get(KnowledgeChallenge, challenge["id"]).best_answer_id == first.id


@pytest.mark.asyncio
async def test_drafts_do_not_qualify_and_private_edits_do_not_replace_public_answer(db, monkeypatch):
    challenge = await published(db)
    await service.write_answer(db, actor("bob"), challenge["id"], "未提交草稿", 0)
    answer = await submitted(db, challenge["id"])
    saved = await service.write_answer(db, actor("alice"), challenge["id"], "新的私有草稿", answer.version)
    result = await service.detail(db, actor("voter"), challenge["id"])
    assert len(result["answers"]) == 1
    assert result["answers"][0]["content"] != saved["content"]
    assert "私有草稿" not in json.dumps(result, ensure_ascii=False)
    after_deadline(monkeypatch)
    await service.settle(db, challenge["id"])
    assert db.session.get(KnowledgeChallenge, challenge["id"]).best_answer_id == answer.id


@pytest.mark.asyncio
async def test_answer_allows_only_one_published_edit_and_drafts_do_not_consume_it(db):
    challenge = await published(db)
    answer = await submitted(db, challenge["id"])
    original_content, original_time = answer.content, answer.submitted_at
    await service.set_like(db, actor("voter"), challenge["id"], True, answer.id)
    mine = (await service.detail(db, actor("alice"), challenge["id"]))["answers"][0]
    assert mine["can_edit"] is True and mine["remaining_edits"] == 1
    for content in ("修订草稿一", "修订草稿二"):
        saved = await service.write_answer(db, actor("alice"), challenge["id"], content, answer.version)
        assert saved["remaining_edits"] == 1 and saved["can_submit"] is True
        assert answer.history == []
        assert answer.content == original_content
    revision_version = answer.version
    revised = await service.write_answer(db, actor("alice"), challenge["id"], "补充后的完整解答", revision_version, submit=True)
    assert revised["remaining_edits"] == 0 and revised["can_submit"] is False
    assert answer.content == "补充后的完整解答"
    assert answer.submitted_at == original_time
    assert len(answer.history) == 1 and answer.history[0]["content"] == original_content
    assert await service.answer_votes(db, answer.id) == 1
    mine = (await service.detail(db, actor("alice"), challenge["id"]))["answers"][0]
    assert mine["can_edit"] is False and mine["remaining_edits"] == 0
    for submit in (False, True):
        with pytest.raises(HTTPException) as error:
            await service.write_answer(db, actor("alice"), challenge["id"], "禁止再次修改", answer.version, submit=submit)
        assert error.value.status_code == 409
        assert "只能修改一次" in error.value.detail
    with pytest.raises(HTTPException) as stale:
        await service.write_answer(db, actor("alice"), challenge["id"], "旧版本重复提交", revision_version, submit=True)
    assert stale.value.status_code == 409
    assert answer.content == "补充后的完整解答" and len(answer.history) == 1
    assert (await service.my_answer(db, actor("bob"), challenge["id"]))["remaining_edits"] == 1
    await submitted(db, challenge["id"], "bob")


@pytest.mark.asyncio
async def test_answer_edit_permission_is_private_and_expires_with_the_question(db, monkeypatch):
    challenge = await published(db)
    answer = await submitted(db, challenge["id"])
    other = (await service.detail(db, actor("voter"), challenge["id"]))["answers"][0]
    assert other["can_edit"] is False and other["remaining_edits"] == 0
    assert (await service.my_answer(db, actor("alice"), challenge["id"]))["can_submit"] is True
    after_deadline(monkeypatch)
    mine = (await service.detail(db, actor("alice"), challenge["id"]))["answers"][0]
    assert mine["can_edit"] is False and mine["remaining_edits"] == 1
    private = await service.my_answer(db, actor("alice"), challenge["id"])
    assert private["can_submit"] is False and private["content"] == answer.content
    with pytest.raises(HTTPException) as error:
        await service.write_answer(db, actor("alice"), challenge["id"], "截止后修改", answer.version, submit=True)
    assert error.value.status_code == 409
    assert answer.history == []


@pytest.mark.asyncio
async def test_no_answers_finish_without_reward(db, monkeypatch):
    challenge = await published(db)
    after_deadline(monkeypatch)
    await service.settle(db, challenge["id"])
    assert db.session.get(KnowledgeChallenge, challenge["id"]).state == "unanswered"
    assert not db.session.scalars(select(RewardRecord)).all()


@pytest.mark.asyncio
async def test_deadline_hides_previews_and_rejects_writes_even_before_worker(db, monkeypatch):
    challenge = await published(db)
    answer = await submitted(db, challenge["id"])
    monkeypatch.setattr(knowledge, "utcnow", lambda: datetime(2026, 11, 21))
    assert (await service.listing(db, actor("voter")))["items"] == []
    for operation in (
        service.set_like(db, actor("voter"), challenge["id"], True),
        service.set_like(db, actor("voter"), challenge["id"], True, answer.id),
        service.write_answer(db, actor("bob"), challenge["id"], "迟到答案", 0, submit=True),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_shared_five_question_quota_works_in_both_directions(db):
    for task_id in range(1, 6):
        seed(db, task_id)
    independent = await published(db)
    await service.set_like(db, actor("voter"), independent["id"], True)
    for task_id in range(1, 5):
        await knowledge.set_like(db, actor("voter"), task_id, True)
    with pytest.raises(HTTPException) as error:
        await knowledge.set_like(db, actor("voter"), 5, True)
    assert error.value.status_code == 429
    second = await published(db, "second")
    with pytest.raises(HTTPException):
        await service.set_like(db, actor("voter"), second["id"], True)
    await service.set_like(db, actor("voter"), independent["id"], False)
    with pytest.raises(HTTPException):
        await service.set_like(db, actor("voter"), second["id"], True)
    await service.set_like(db, actor("voter"), independent["id"], True)


@pytest.mark.asyncio
async def test_task_linked_votes_reuse_the_same_fact_and_original_month(db):
    seed(db, 1, created=datetime(2026, 8, 2))
    challenge = await published(db, task_id=1)
    assert challenge["asked_at"].startswith("2026-08-02")
    await service.set_like(db, actor("voter"), challenge["id"], True)
    result = await knowledge.set_like(db, actor("voter"), 1, True)
    assert result["valid_like_count"] == 1
    assert (await service.detail(db, actor("voter"), challenge["id"]))["like_count"] == 1
    with pytest.raises(HTTPException):
        await published(db, key="duplicate", task_id=1)


@pytest.mark.asyncio
async def test_self_likes_and_stale_answer_versions_are_rejected(db):
    challenge = await published(db)
    answer = await submitted(db, challenge["id"])
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor("alice"), challenge["id"], True, answer.id)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await service.write_answer(db, actor("alice"), challenge["id"], "覆盖", 0)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_override_keeps_original_finance_record_and_audit(db, monkeypatch):
    challenge = await published(db)
    await submitted(db, challenge["id"], "alice")
    second = await submitted(db, challenge["id"], "bob")
    after_deadline(monkeypatch)
    await service.settle(db, challenge["id"])
    original_reward = db.session.scalar(select(RewardRecord))
    original = (original_reward.user_id, original_reward.amount, original_reward.status)
    record = db.session.get(KnowledgeChallenge, challenge["id"])
    updated = await service.override(db, admin(), challenge["id"], OverrideBody(
        action="replace", answer_id=second.id, reason="原答案存在错误", version=record.version))
    assert updated["best_answer"]["id"] == second.id
    assert updated["reward_attention"] is True
    assert (original_reward.user_id, original_reward.amount, original_reward.status) == original
    assert len(db.session.scalars(select(RewardRecord)).all()) == 1
    assert db.session.scalars(select(KnowledgeChallengeAudit).where(
        KnowledgeChallengeAudit.action == "replace")).one().reason == "原答案存在错误"
    await service.override(db, admin(), challenge["id"], OverrideBody(
        action="reject", reason="结果不予通过", version=updated["version"]))
    assert db.session.get(KnowledgeChallenge, challenge["id"]).best_answer_id is None


@pytest.mark.asyncio
async def test_pause_still_settles_and_ordinary_users_cannot_manage(db, monkeypatch):
    challenge = await published(db)
    await submitted(db, challenge["id"])
    with pytest.raises(HTTPException) as error:
        await service.change_state(db, actor("alice"), challenge["id"], StateBody(
            action="pause", reason="暂停", version=challenge["version"]))
    assert error.value.status_code == 403
    await service.change_state(db, admin(), challenge["id"], StateBody(
        action="pause", reason="补充问题信息", version=challenge["version"]))
    after_deadline(monkeypatch)
    assert await service.settle(db, challenge["id"])


@pytest.mark.asyncio
async def test_public_payloads_show_author_but_redact_sensitive_content_and_do_not_leak_drafts(db):
    challenge = await published(db)
    await submitted(db, challenge["id"], content="提单甲 alice-account 联系13800138000 https://private.example/a")
    await service.write_answer(db, actor("bob"), challenge["id"], "未发布的私人草稿", 0)
    result = await service.detail(db, actor("voter"), challenge["id"])
    assert len(result["answers"]) == 1
    assert result["answers"][0]["author"] == "提单甲"
    for secret in ("提单甲", "alice-account", "13800138000", "private.example"):
        assert secret not in result["answers"][0]["content"]
    serialized = json.dumps(result, ensure_ascii=False)
    for secret in ("未发布的私人草稿", "author_id", "publisher_id", "questioner_id"):
        assert secret not in serialized
    assert result["can_manage_state"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", [None, 1])
@pytest.mark.parametrize("completed", [False, True])
async def test_answer_author_is_visible_for_all_readers_and_community_results(db, monkeypatch, task_id, completed):
    if task_id is not None:
        seed(db, task_id)
    challenge = await published(db, task_id=task_id)
    await submitted(db, challenge["id"], user_id="bob")
    if completed:
        after_deadline(monkeypatch)
        await service.settle(db, challenge["id"])
    for reader in (actor("bob"), actor("voter"), admin()):
        result = await service.detail(db, reader, challenge["id"])
        assert result["answers"][0]["author"] == "接单乙"
        assert result["questioner"] == ("匿名提问人" if task_id else "平台发布")
        if completed:
            assert result["best_answer"]["author"] == "接单乙"
            community = await service.community_result(db, reader, challenge["id"])
            assert community["best_answer"]["author"] == "接单乙"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, ""])
async def test_answer_author_falls_back_to_username_when_name_is_empty(db, name):
    challenge = await published(db)
    await submitted(db, challenge["id"])
    user = await db.get(UserDB, "alice")
    user.name = name
    await db.commit()
    result = await service.detail(db, actor("voter"), challenge["id"])
    assert result["answers"][0]["author"] == "alice-account"


@pytest.mark.asyncio
async def test_answer_stays_readable_when_author_account_is_missing(db):
    challenge = await published(db)
    await submitted(db, challenge["id"])
    db.session.delete(await db.get(UserDB, "alice"))
    await db.commit()
    result = await service.detail(db, actor("voter"), challenge["id"])
    assert result["answers"][0]["author"] == "未知用户"


def test_migration_is_idempotent_and_supports_multiple_independent_questions():
    path = Path(__file__).parents[1] / "alembic/versions/e1f4a7b0c326_knowledge_challenges.py"
    spec = importlib.util.spec_from_file_location("challenge_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert "knowledge_challenges" in inspect(connection).get_table_names()
            migration.downgrade()
            assert "knowledge_challenges" not in inspect(connection).get_table_names()
    engine.dispose()


def test_api_forbids_non_admin_create_and_accepts_standalone_payload(db):
    app = FastAPI()
    app.include_router(router)

    async def get_db():
        yield db

    app.dependency_overrides[get_async_db] = get_db
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor("alice")
    client = TestClient(app)
    body = {"request_key": "api", "title": "独立题目", "content": "无需工单", "bounty_fen": 100,
            "ends_at": "2026-11-21T00:00:00Z"}
    assert client.post("/knowledge/challenges", json=body).status_code == 403
    app.dependency_overrides[get_current_active_user_from_token] = admin
    response = client.post("/knowledge/challenges", json=body)
    assert response.status_code == 201
    assert response.json()["source"] == "standalone"
    assert client.post("/knowledge/challenges", json=body).json()["id"] == response.json()["id"]
    assert client.post("/knowledge/challenges", json={**body, "bounty_fen": 200}).status_code == 409


@pytest.mark.asyncio
async def test_cancelled_questions_are_visible_in_ended_before_original_deadline(db):
    challenge = await published(db)
    await service.change_state(db, admin(), challenge["id"], StateBody(
        action="cancel", reason="取消征集", version=challenge["version"]))
    assert not (await service.listing(db, actor("voter")))["items"]
    assert (await service.listing(db, actor("voter"), "ended"))["items"][0]["id"] == challenge["id"]


@pytest.mark.asyncio
async def test_linked_task_participants_cannot_bypass_existing_self_vote_rule(db):
    seed(db, 1)
    challenge = await published(db, task_id=1)
    for user_id in ("alice", "bob", "reviewer"):
        with pytest.raises(HTTPException) as error:
            await service.set_like(db, actor(user_id), challenge["id"], True)
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_worker_retries_failed_settlement_without_duplicate_rewards(db, monkeypatch):
    from app.modules.admin.services import knowledge_challenge_tasks as tasks

    challenge = await published(db)
    await submitted(db, challenge["id"])
    after_deadline(monkeypatch)

    @asynccontextmanager
    async def session_factory():
        yield db

    async def failure(*args):
        raise RuntimeError("isolated failure")

    monkeypatch.setattr(tasks, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(tasks, "settle", failure)
    await tasks.run_once()
    assert db.session.get(KnowledgeChallenge, challenge["id"]).last_error
    assert not db.session.scalars(select(RewardRecord)).all()
    monkeypatch.setattr(tasks, "settle", service.settle)
    await tasks.run_once()
    await tasks.run_once()
    assert db.session.get(KnowledgeChallenge, challenge["id"]).last_error is None
    assert len(db.session.scalars(select(RewardRecord)).all()) == 1


@pytest.mark.asyncio
async def test_answer_like_quota_is_separate_and_cancellation_does_not_refund(db):
    challenges = [await published(db, key=f"answer-{position}") for position in range(6)]
    answers = [await submitted(db, challenge["id"]) for challenge in challenges]
    for challenge, answer in zip(challenges[:5], answers[:5]):
        await service.set_like(db, actor("voter"), challenge["id"], True, answer.id)
    await service.set_like(db, actor("voter"), challenges[0]["id"], False, answers[0].id)
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor("voter"), challenges[5]["id"], True, answers[5].id)
    assert error.value.status_code == 429
    await service.set_like(db, actor("voter"), challenges[5]["id"], True)


@pytest.mark.asyncio
async def test_feature_switch_blocks_new_participation_and_grants_but_preserves_reading(db, monkeypatch):
    challenge = await published(db)
    await submitted(db, challenge["id"])
    monkeypatch.setattr(service.settings, "KNOWLEDGE_CHALLENGES_ENABLED", False)
    assert (await service.detail(db, actor("voter"), challenge["id"]))["can_answer"] is False
    with pytest.raises(HTTPException):
        await service.write_answer(db, actor("bob"), challenge["id"], "暂停期间", 0, submit=True)
    after_deadline(monkeypatch)
    assert await service.settle(db, challenge["id"]) is False
    assert not db.session.scalars(select(RewardRecord)).all()
