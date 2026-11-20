from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.knowledge import KnowledgeLike, KnowledgePublication, KnowledgeSettlement
from app.models.task import TaskStatus
from app.models.wxpay import RewardRecord, RewardStatus
from app.modules.admin.api.knowledge import router
from app.modules.admin.services import knowledge_service as service
from app.modules.tasks.services.archive_report_service import ArchiveReportService
from app.modules.wxpay import archive_finance
from backend.tests.test_knowledge import actor, db, seed


def seed_contributors(db):
    for task_id, (questioner, answerer) in enumerate([
        ('alice', 'bob'), ('alice', 'bob'), ('bob', 'reviewer'),
        ('voter', 'bob'), ('alice', 'reviewer'), ('voter', 'alice'),
    ], 1):
        seed(db, task_id, questioner, answerer)


@pytest.mark.asyncio
@pytest.mark.parametrize('role,expected', [
    ('questioner', ['alice', 'voter', 'bob']),
    ('answerer', ['bob', 'reviewer', 'alice']),
])
async def test_role_rankings_count_only_the_selected_contribution(db, role, expected):
    seed_contributors(db)
    seed(db, 7, 'reviewer', 'voter', created=datetime(2026, 12, 1))
    seed(db, 8, 'reviewer', 'voter', created=datetime(2026, 7, 31))
    task, report = seed(db, 9, 'reviewer', 'voter')
    task.status = TaskStatus.ARCHIVING
    await db.commit()

    entries = await service.public_rankings(db, '2026-11', role=role)
    count_field = 'question_count' if role == 'questioner' else 'answer_count'
    assert [entry['user_id'] for entry in entries] == expected
    assert [entry[count_field] for entry in entries] == [3, 2, 1]
    assert [entry['rank'] for entry in entries] == [1, 2, 3]
    assert all(entry['estimated_reward_fen'] == 0 for entry in entries)
    overview = await service.overview(db, actor('voter'), '2026-11')
    key = 'top_questioners' if role == 'questioner' else 'top_answerers'
    assert overview[key] == entries
    assert len(overview['top_three']) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['questioner', 'answerer'])
async def test_role_ties_use_likes_from_that_role_only(db, role):
    participants = [('alice', 'bob'), ('voter', 'alice'), ('reviewer', 'alice')]
    for task_id, (questioner, answerer) in enumerate(participants, 1):
        if role == 'answerer':
            questioner, answerer = answerer, questioner
        seed(db, task_id, questioner, answerer)
    for task_id, voters in [(1, ['reviewer']), (2, ['bob', 'reviewer']), (3, ['bob', 'voter'])]:
        for voter in voters:
            db.add(KnowledgeLike(task_id=task_id, user_id=voter, active=True, risk_status='valid', updated_at=service.utcnow()))
    await db.commit()

    assert (await service.rankings(db, '2026-11'))[0]['user_id'] == 'alice'
    entries = await service.rankings(db, '2026-11', role=role)
    assert [entry['user_id'] for entry in entries] == ['reviewer', 'voter', 'alice']
    assert [entry['valid_like_count'] for entry in entries] == [2, 2, 1]
    assert [entry['tied'] for entry in entries] == [True, True, False]


@pytest.mark.asyncio
async def test_role_rankings_do_not_reuse_or_mutate_combined_settlement(db):
    seed(db)
    combined = await service.rankings(db, '2026-11')
    snapshot = KnowledgeSettlement(month='2026-11', revision=1, status='granted',
        rankings=combined, audit=[], frozen_at=datetime(2026, 12, 1))
    db.add(snapshot)
    await db.commit()
    seed(db, 2, 'alice', 'reviewer')

    questioners = await service.rankings(db, '2026-11', role='questioner')
    answerers = await service.rankings(db, '2026-11', role='answerer')
    assert [(entry['user_id'], entry['question_count']) for entry in questioners] == [('alice', 2)]
    assert {entry['user_id'] for entry in answerers} == {'bob', 'reviewer'}
    assert await service.rankings(db, '2026-11') == combined
    assert snapshot.rankings == combined
    assert db.session.scalars(select(RewardRecord)).all() == []


@pytest.mark.asyncio
async def test_dual_role_user_can_appear_once_in_each_role_board(db):
    seed(db, questioner='alice', answerer='alice')
    for role, count_field in [('questioner', 'question_count'), ('answerer', 'answer_count')]:
        entries = await service.rankings(db, '2026-11', role=role)
        assert len(entries) == 1
        assert entries[0]['user_id'] == 'alice'
        assert entries[0][count_field] == 1
    assert (await service.rankings(db, '2026-11'))[0]['contribution_count'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [True, False])
async def test_role_boards_require_finance_closure_without_waiting_for_withdrawal(db, monkeypatch, approved):
    task, report = seed(db)
    db.session.delete(await db.get(KnowledgePublication, task.id))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    task.archived_by, task.archived_at = None, None
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'evaluate', AsyncMock(return_value=False))
    await db.commit()
    await ArchiveReportService.approve(db, report.id, 'reviewer', '通过意见', '可复用价值', 1)
    await db.commit()
    for role in ('questioner', 'answerer'):
        assert await service.rankings(db, '2026-11', role=role) == []

    rewards = db.session.scalars(select(RewardRecord)).all()
    assert len(rewards) == 3
    await archive_finance.audit_archive_rewards(db, [rewards[0].id], 'finance', approved, '财务意见')
    await db.commit()

    for role, user_id in [('questioner', 'alice'), ('answerer', 'bob')]:
        entries = await service.rankings(db, '2026-11', role=role)
        assert [entry['user_id'] for entry in entries] == ([user_id] if approved else [])
    assert all(reward.status == (RewardStatus.PENDING if approved else RewardStatus.REJECTED) for reward in rewards)


def test_rankings_api_exposes_independent_boards_and_keeps_combined_default(db):
    seed_contributors(db)
    app = FastAPI()
    app.include_router(router)

    async def database():
        yield db

    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('voter')
    with TestClient(app) as client:
        questioners = client.get('/knowledge/rankings?month=2026-11&role=questioner')
        answerers = client.get('/knowledge/rankings?month=2026-11&role=answerer')
        assert questioners.status_code == answerers.status_code == 200
        assert [entry['user_id'] for entry in questioners.json()['items']] == ['alice', 'voter', 'bob']
        assert [entry['user_id'] for entry in answerers.json()['items']] == ['bob', 'reviewer', 'alice']
        assert client.get('/knowledge/rankings?month=2026-11').json()['items'] == client.get('/knowledge/rankings?month=2026-11&role=all').json()['items']
        assert client.get('/knowledge/rankings?month=2026-11&role=reviewer').status_code == 422
        overview = client.get('/knowledge/overview?month=2026-11').json()
        assert overview['top_questioners'] == questioners.json()['items']
        assert overview['top_answerers'] == answerers.json()['items']
