from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.admin.api.auth import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.knowledge import KnowledgePublication
from app.models.task import TaskOperationLog, TaskStatus
from app.models.wxpay import RewardConfig, RewardRecord, RewardStatus
from app.modules.admin.services import knowledge_service
from app.modules.tasks.services.archive_report_service import ArchiveReportService
from app.modules.tasks.services.ticket_service import TicketService
from app.modules.wxpay import archive_finance
from app.modules.wxpay.api import admin
from backend.tests.test_knowledge import actor, db, seed


async def prepare(db, monkeypatch, questioner='alice', answerer='bob'):
    task, report = seed(db, questioner=questioner, answerer=answerer)
    db.session.delete(await db.get(KnowledgePublication, task.id))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    monkeypatch.setattr(admin.AuditDecisionEngine, 'evaluate', AsyncMock(return_value=True))
    await ArchiveReportService.approve(db, report.id, 'reviewer', '通过意见', '具有复用价值', 1)
    await db.commit()
    return task, report, db.session.scalars(select(RewardRecord)).all()


@pytest.mark.asyncio
async def test_content_approval_creates_three_distinct_roles_without_auto_pass(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    assert {(reward.role, reward.user_id, reward.amount) for reward in rewards} == {
        ('reporter', 'alice', 200), ('assignee', 'bob', 300), ('reviewer', 'reviewer', 100)}
    assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards)
    assert all(reward.source_type == archive_finance.ARCHIVE_FINANCE_SOURCE for reward in rewards)
    admin.AuditDecisionEngine.evaluate.assert_not_awaited()
    assert (await knowledge_service.question_items(db, actor('voter'), '2026-11'))['total'] == 0
    assert await knowledge_service.rankings(db, '2026-11') == []
    assert task.status == TaskStatus.ARCHIVING
    assert report.submit_status == 'approved'
    with pytest.raises(HTTPException) as blocked:
        await ArchiveReportService.start_archive(db, task.id, 'bob')
    assert blocked.value.status_code == 409


@pytest.mark.asyncio
async def test_finance_approval_is_whole_task_idempotent_and_uses_question_month(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(knowledge_service, 'utcnow', lambda: datetime(2026, 11, 30, 16))
    processed = await archive_finance.audit_archive_rewards(db, [rewards[0].id], 'voter', True, '金额核对无误')
    await db.commit()
    assert len(processed) == 3
    assert all(reward.status == RewardStatus.PENDING for reward in rewards)
    assert all(reward.audit_by == 'voter' and reward.audit_remark == '金额核对无误' for reward in rewards)
    assert all(reward.expire_at > datetime(2026, 11, 30, 16) for reward in rewards)
    assert task.status == TaskStatus.ARCHIVED
    assert task.archived_at == datetime(2026, 11, 30, 16)
    publication = await db.get(KnowledgePublication, task.id)
    assert publication.report_id == report.id
    assert publication.first_approved_at == task.archived_at
    assert {entry['user_id'] for entry in await knowledge_service.rankings(db, '2026-11')} == {'alice', 'bob'}
    assert await knowledge_service.rankings(db, '2026-12') == []
    assert 'reviewer' not in str(await knowledge_service.question_items(db, actor('voter'), '2026-11'))
    assert len(db.session.scalars(select(TaskOperationLog)).all()) == 1
    assert await archive_finance.audit_archive_rewards(db, [reward.id for reward in rewards], 'voter', True) == []
    assert len(db.session.scalars(select(RewardRecord)).all()) == 3


@pytest.mark.asyncio
async def test_finance_rejection_blocks_all_rewards_and_publication(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    processed = await archive_finance.audit_archive_rewards(db, [rewards[1].id], 'voter', False, '奖励资料不完整')
    await db.commit()
    assert len(processed) == 3
    assert all(reward.status == RewardStatus.REJECTED for reward in rewards)
    assert all(reward.audit_remark == '奖励资料不完整' for reward in rewards)
    assert task.status == TaskStatus.ARCHIVING
    assert report.submit_status == 'approved'
    assert await db.get(KnowledgePublication, task.id) is None
    assert await knowledge_service.rankings(db, '2026-11') == []


@pytest.mark.asyncio
async def test_same_user_question_and_answer_rewards_do_not_collide(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch, answerer='alice')
    assert sum(reward.amount for reward in rewards if reward.user_id == 'alice') == 500
    assert len(rewards) == 3


@pytest.mark.asyncio
async def test_invalid_reward_config_does_not_finish_content_review(db):
    task, report = seed(db)
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    config = await db.get(RewardConfig, 'task_archive')
    config.enabled = False
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await ArchiveReportService.approve(db, report.id, 'reviewer', '意见', '价值点', 1)
    assert denied.value.status_code == 422
    assert report.submit_status == 'submitted'
    assert not db.session.scalars(select(RewardRecord)).all()


@pytest.mark.asyncio
async def test_incomplete_group_cannot_release_any_rewards(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    db.session.delete(rewards[2])
    await db.commit()
    with pytest.raises(HTTPException) as denied:
        await archive_finance.audit_archive_rewards(db, [rewards[0].id], 'voter', True)
    assert denied.value.status_code == 409
    assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards[:2])
    assert task.status == TaskStatus.ARCHIVING


@pytest.mark.asyncio
async def test_regular_task_status_api_cannot_bypass_finance(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(TicketService, 'get_ticket_by_id', AsyncMock(return_value=task))
    with pytest.raises(HTTPException) as denied:
        await TicketService.update_ticket_status(db, task.id, TaskStatus.ARCHIVED)
    assert denied.value.status_code == 409
    assert task.status == TaskStatus.ARCHIVING


@pytest.mark.asyncio
async def test_finance_queue_uses_existing_api_and_requires_finance_permission(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(admin, 'ensure_permission', MagicMock())
    app = FastAPI()
    app.include_router(admin.router)
    async def database():
        yield db
    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('alice')
    with TestClient(app) as client:
        assert client.get('/admin/archive-finance/1').status_code == 403
        assert client.post('/admin/rewards/batch-audit', json={'ids': [rewards[0].id]}).status_code == 403
        finance_actor = {**actor('voter'), 'permissions': [admin.PERM_AUDIT, admin.PERM]}
        app.dependency_overrides[get_current_active_user_from_token] = lambda: finance_actor
        queue = client.get('/admin/rewards?status=pending_audit')
        assert queue.status_code == 200
        assert queue.json()['total'] == 3
        assert {item['source_id'] for item in queue.json()['items']} == {'1'}
        detail = client.get('/admin/archive-finance/1')
        assert detail.status_code == 200
        assert detail.json()['content'] == report.content
        assert detail.json()['total_amount'] == 600
        assert 'reviewer' not in detail.text
        assert client.post('/admin/rewards/batch-reject', json={'ids': [rewards[0].id], 'audit_remark': ' '}).status_code == 422
        response = client.post('/admin/rewards/batch-audit', json={'ids': [rewards[0].id]})
        assert response.status_code == 200
        assert response.json()['ok_count'] == 3
        assert response.json()['skipped_ids'] == []
        assert task.status == TaskStatus.ARCHIVED
        assert client.post('/admin/rewards/batch-audit', json={'ids': [rewards[0].id]}).json()['ok_count'] == 0


@pytest.mark.asyncio
async def test_existing_non_archive_rewards_can_be_audited_with_archive_group(db, monkeypatch):
    from app.modules.wxpay.schemas import GrantRewardIn

    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(admin, 'ensure_permission', MagicMock())
    ordinary, duplicated = await admin.RewardService.grant(db, GrantRewardIn(user_id='alice',
        amount=100, scene_name='普通奖励', reward_desc='保持原财务链路',
        source_type='admin_test', source_id='test-normal'), role='manual')
    assert duplicated is False
    result = await admin.batch_audit(admin.BatchAuditIn(ids=[ordinary.id, rewards[0].id]),
        db=db, current_user=actor('voter'))
    assert result.ok_count == 4
    assert ordinary.status == RewardStatus.PENDING
    assert ordinary.audit_by == 'voter'
    assert task.status == TaskStatus.ARCHIVED


@pytest.mark.asyncio
async def test_existing_auto_pass_endpoint_cannot_bypass_manual_archive_finance(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(admin, 'ensure_permission', MagicMock())
    db.add(RewardConfig(config_key='audit_policy', enabled=True, config={'auto_pass_max_fen': 10000}))
    await db.commit()
    result = await admin.run_auto_pass(db=db, current_user=actor('voter'))
    assert result.ok_count == 0
    assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards)
    assert task.status == TaskStatus.ARCHIVING
    admin.AuditDecisionEngine.evaluate.assert_not_awaited()
