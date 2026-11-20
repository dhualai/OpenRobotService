import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.knowledge import KnowledgeLike, KnowledgeLikeEvent, KnowledgePublication
from app.modules.admin.api.knowledge import router
from app.modules.admin.services import knowledge_service as service
from backend.tests.test_knowledge import AsyncDb, actor, db, seed


def seed_questions(db, count=6):
    for task_id in range(1, count + 1):
        seed(db, task_id)


@pytest.mark.asyncio
async def test_five_questions_allowed_and_sixth_rejected_without_writes(db):
    seed_questions(db)
    for task_id in range(1, 6):
        result = await service.set_like(db, actor('voter'), task_id, True)
        assert result['liked_by_me'] is True
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, {'username': 'voter-account'}, 6, True)
    assert error.value.status_code == 429
    assert '5个不同问题' in error.value.detail
    assert db.session.get(KnowledgeLike, (6, 'voter')) is None
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 5
    assert len(await service.like_state(db)) == 5


@pytest.mark.asyncio
async def test_cancel_does_not_restore_quota_and_same_question_can_be_reliked(db):
    seed_questions(db)
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    await service.set_like(db, actor('voter'), 1, False)
    await service.set_like(db, actor('voter'), 1, False)
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 6, True)
    assert error.value.status_code == 429
    result = await service.set_like(db, actor('voter'), 1, True)
    await service.set_like(db, actor('voter'), 1, True)
    assert result['liked_by_me'] is True
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 7
    assert len(await service.like_state(db)) == 5


@pytest.mark.asyncio
async def test_cancelling_unliked_questions_does_not_consume_quota(db):
    seed_questions(db)
    await service.set_like(db, actor('voter'), 6, False)
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 5


@pytest.mark.asyncio
async def test_daily_quota_is_independent_for_each_canonical_user(db):
    seed_questions(db)
    db.add(UserDB(id='other-voter', username='other-voter-account', name='另一用户',
                  status='active', password_hash='test'))
    await db.commit()
    for user_id in ('voter', 'other-voter'):
        for task_id in range(1, 6):
            await service.set_like(db, actor(user_id), task_id, True)
        with pytest.raises(HTTPException) as error:
            await service.set_like(db, actor(user_id), 6, True)
        assert error.value.status_code == 429
    assert len(await service.like_state(db)) == 10


@pytest.mark.asyncio
async def test_quota_resets_at_beijing_midnight_and_relikes_count_on_new_day(db, monkeypatch):
    seed_questions(db, 10)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 20, 15, 59, 59, 999999))
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 20, 16))
    await service.set_like(db, actor('voter'), 6, True)
    await service.set_like(db, actor('voter'), 1, False)
    await service.set_like(db, actor('voter'), 1, True)
    for task_id in range(7, 10):
        await service.set_like(db, actor('voter'), task_id, True)
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 10, True)
    assert error.value.status_code == 429
    assert len(await service.like_state(db)) == 9


@pytest.mark.asyncio
async def test_utc_midnight_does_not_reset_beijing_day(db, monkeypatch):
    seed_questions(db)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 19, 23, 59, 59))
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 20))
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 6, True)
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_old_active_likes_and_idempotent_requests_do_not_consume_today_quota(db, monkeypatch):
    seed_questions(db, 11)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 19))
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 20))
    await service.set_like(db, actor('voter'), 1, True)
    for task_id in range(6, 11):
        await service.set_like(db, actor('voter'), task_id, True)
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 11, True)
    assert error.value.status_code == 429
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 10


@pytest.mark.asyncio
async def test_invalidated_likes_do_not_restore_daily_quota(db):
    seed_questions(db)
    for task_id in range(1, 6):
        await service.set_like(db, actor('voter'), task_id, True)
    db.session.get(KnowledgeLike, (1, 'voter')).risk_status = 'invalid'
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 6, True)
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_rejected_likes_do_not_consume_daily_quota(db):
    seed(db, 1, questioner='voter')
    for task_id in range(2, 9):
        seed(db, task_id)
    db.session.get(KnowledgePublication, 2).active = False
    await db.commit()
    for task_id, status in ((1, 403), (2, 404)):
        with pytest.raises(HTTPException) as error:
            await service.set_like(db, actor('voter'), task_id, True)
        assert error.value.status_code == status
    assert not db.session.scalars(select(KnowledgeLikeEvent)).all()
    for task_id in range(3, 8):
        await service.set_like(db, actor('voter'), task_id, True)
    with pytest.raises(HTTPException) as error:
        await service.set_like(db, actor('voter'), 8, True)
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_user_row_is_locked_before_any_snapshot_read(db, monkeypatch):
    seed(db)
    execute = AsyncMock(wraps=db.execute)
    monkeypatch.setattr(db, 'execute', execute)
    await service.set_like(db, actor('voter'), 1, True)
    first_query = execute.await_args_list[0].args[0]
    sql = str(first_query.compile(dialect=mysql.dialect()))
    assert 'FROM users' in sql
    assert sql.endswith('FOR UPDATE')


@pytest.mark.asyncio
async def test_concurrent_different_question_likes_share_user_lock(db):
    seed_questions(db)
    for task_id in range(1, 5):
        await service.set_like(db, actor('voter'), task_id, True)
    await db.rollback()
    user_lock = asyncio.Lock()

    class LockedDb(AsyncDb):
        holding_lock = False

        async def execute(self, query):
            if query._for_update_arg is not None and query.column_descriptions[0]['entity'] is UserDB:
                await user_lock.acquire()
                self.holding_lock = True
            result = await super().execute(query)
            await asyncio.sleep(0)
            return result

        def release_lock(self):
            if self.holding_lock:
                self.holding_lock = False
                user_lock.release()

        async def commit(self):
            await super().commit()
            self.release_lock()

        async def rollback(self):
            await super().rollback()
            self.release_lock()

    async def like_question(task_id):
        with Session(db.session.get_bind(), expire_on_commit=False) as session:
            concurrent_db = LockedDb(session)
            try:
                await service.set_like(concurrent_db, actor('voter'), task_id, True)
                return 200
            except HTTPException as error:
                return error.status_code
            finally:
                await concurrent_db.rollback()

    results = await asyncio.wait_for(asyncio.gather(like_question(5), like_question(6)), timeout=5)
    assert sorted(results) == [200, 429]
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 5
    assert len(await service.like_state(db)) == 5


def test_api_enforces_daily_quota_and_keeps_cancel_and_retry_available(db):
    seed_questions(db)
    app = FastAPI()
    app.include_router(router)

    async def database():
        yield db

    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('voter')
    with TestClient(app) as client:
        for task_id in range(1, 6):
            assert client.put(f'/knowledge/questions/{task_id}/like').status_code == 200
        response = client.put('/knowledge/questions/6/like')
        assert response.status_code == 429
        assert '5个不同问题' in response.json()['detail']
        assert client.put('/knowledge/questions/1/like').status_code == 200
        assert client.delete('/knowledge/questions/1/like').status_code == 200
        assert client.put('/knowledge/questions/6/like').status_code == 429
        assert client.put('/knowledge/questions/1/like').status_code == 200
