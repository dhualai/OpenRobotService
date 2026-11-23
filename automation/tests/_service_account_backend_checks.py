"""Isolated business checks run with the backend's SQLite test fixtures."""

from datetime import timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.task import Task
from app.models.wxpay import RewardRecord
from app.modules.admin.api.knowledge_challenge import CreateChallenge
from app.modules.admin.services import knowledge_challenge as challenges
from backend.tests.test_knowledge import actor, db  # noqa: F401
from backend.tests.test_knowledge_challenge import after_deadline, submitted


async def _publish(db):
    request = CreateChallenge(
        request_key="service-account-business-check",
        title="机器人定位漂移",
        content="请协助排查长期定位漂移",
        bounty_fen=10000,
        ends_at=(challenges.utcnow() + timedelta(days=1)).replace(tzinfo=timezone.utc),
    )
    manager = {**actor("reviewer"), "permissions": ["backend:knowledge-challenge:manage"]}
    draft = await challenges.create(db, manager, request)
    return await challenges.publish(db, manager, draft["id"], draft["version"])


@pytest.mark.asyncio
async def test_manager_can_publish_standalone_challenge(db):
    draft = CreateChallenge(
        request_key="service-account-unprivileged",
        title="机器人定位漂移",
        content="请协助排查长期定位漂移",
        bounty_fen=10000,
        ends_at=(challenges.utcnow() + timedelta(days=1)).replace(tzinfo=timezone.utc),
    )
    with pytest.raises(HTTPException) as error:
        await challenges.create(db, actor("alice"), draft)
    assert error.value.status_code == 403

    question = await _publish(db)
    assert question["source"] == "standalone"
    assert question["questioner"] == "平台发布"
    assert not db.session.scalars(select(Task)).all()


@pytest.mark.asyncio
async def test_voted_answer_is_rewarded_once_after_deadline(db, monkeypatch):
    question = await _publish(db)
    question_id = question["id"]
    await submitted(db, question_id, "alice")
    winner = await submitted(db, question_id, "bob", "校验定位参数和传感器")
    await challenges.set_like(db, actor("voter"), question_id, True)
    await challenges.set_like(db, actor("voter"), question_id, True, winner.id)

    after_deadline(monkeypatch)
    assert await challenges.settle(db, question_id) is True
    assert await challenges.settle(db, question_id) is False
    result = await challenges.detail(db, actor("voter"), question_id)
    assert result["best_answer"]["id"] == winner.id
    record = db.session.scalars(select(RewardRecord)).one()
    assert (record.source_type, record.source_id, record.user_id, record.amount, record.status) == (
        "knowledge_challenge", str(question_id), "bob", 10000, "pending_audit",
    )
