import json
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
async def test_finance_approval_per_id_independent_delayed_task_archive(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(knowledge_service, 'utcnow', lambda: datetime(2026, 11, 30, 16))
    # 第一步：只选 1 条 archive reward → 只过这一条，task 保持 ARCHIVING
    processed = await archive_finance.audit_archive_rewards(db, [rewards[0].id], 'voter', True, '金额核对无误')
    await db.commit()
    assert len(processed) == 1
    assert rewards[0].status == RewardStatus.PENDING
    assert rewards[0].audit_by == 'voter'
    assert rewards[0].audit_remark == '金额核对无误'
    assert rewards[0].expire_at > datetime(2026, 11, 30, 16)
    # 另外两条未被触及
    assert rewards[1].status == RewardStatus.PENDING_AUDIT
    assert rewards[2].status == RewardStatus.PENDING_AUDIT
    # 还未全部终态 → task 保持 ARCHIVING，不 publish
    assert task.status == TaskStatus.ARCHIVING
    assert await db.get(KnowledgePublication, task.id) is None
    assert len(db.session.scalars(select(TaskOperationLog)).all()) == 1

    # 第二步：过剩下两条
    processed2 = await archive_finance.audit_archive_rewards(db, [rewards[1].id, rewards[2].id], 'voter', True, '金额核对无误')
    await db.commit()
    assert len(processed2) == 2
    assert all(r.status == RewardStatus.PENDING for r in rewards)
    # 全部终态 + 有 approved → 推进 ARCHIVING → ARCHIVED 并 publish
    assert task.status == TaskStatus.ARCHIVED
    assert task.archived_at == datetime(2026, 11, 30, 16)
    publication = await db.get(KnowledgePublication, task.id)
    assert publication.report_id == report.id
    assert publication.first_approved_at == task.archived_at
    assert {entry['user_id'] for entry in await knowledge_service.rankings(db, '2026-11')} == {'alice', 'bob'}
    assert len(db.session.scalars(select(TaskOperationLog)).all()) == 2

    # 幂等：重复调用返回空
    assert await archive_finance.audit_archive_rewards(db, [r.id for r in rewards], 'voter', True) == []


@pytest.mark.asyncio
async def test_finance_rejection_per_id_independent(db, monkeypatch):
    """驳回按选中的 ids 逐条处理，剩余 pending_audit 不受影响。"""
    task, report, rewards = await prepare(db, monkeypatch)
    processed = await archive_finance.audit_archive_rewards(db, [rewards[1].id], 'voter', False, '奖励资料不完整')
    await db.commit()
    assert len(processed) == 1
    # 仅选中的那条被驳回
    assert rewards[1].status == RewardStatus.REJECTED
    assert rewards[1].audit_remark == '奖励资料不完整'
    # 另外两条仍是 pending_audit
    assert rewards[0].status == RewardStatus.PENDING_AUDIT
    assert rewards[2].status == RewardStatus.PENDING_AUDIT
    # 还有 pending_audit 未处理 → task 保持 ARCHIVING，不 publish
    assert task.status == TaskStatus.ARCHIVING
    assert report.submit_status == 'approved'
    assert await db.get(KnowledgePublication, task.id) is None
    assert await knowledge_service.rankings(db, '2026-11') == []

    # 再过剩余两条 → 全部终态但没有 approved（全是 rejected）→ task 仍不 publish
    await archive_finance.audit_archive_rewards(db, [rewards[0].id, rewards[2].id], 'voter', False, '驳回')
    await db.commit()
    assert all(r.status == RewardStatus.REJECTED for r in rewards)
    assert task.status == TaskStatus.ARCHIVING  # 没有任何 approved，不推进
    assert await db.get(KnowledgePublication, task.id) is None


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
async def test_archive_reward_audit_works_even_if_siblings_missing(db, monkeypatch):
    """archive reward 审核不再校验三方完整性，单条可独立通过。"""
    task, report, rewards = await prepare(db, monkeypatch)
    db.session.delete(rewards[2])
    await db.commit()
    processed = await archive_finance.audit_archive_rewards(db, [rewards[0].id], 'voter', True)
    await db.commit()
    assert len(processed) == 1
    assert rewards[0].status == RewardStatus.PENDING
    assert rewards[1].status == RewardStatus.PENDING_AUDIT
    assert task.status == TaskStatus.ARCHIVING
    assert await db.get(KnowledgePublication, task.id) is None


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
        assert response.json()['ok_count'] == 1  # 只按选中 ids 处理，不再整单
        assert response.json()['skipped_ids'] == []
        assert task.status == TaskStatus.ARCHIVING  # 还有 2 条 pending_audit，不推进
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
    assert result.ok_count == 2  # 1 条普通 + 1 条 archive，不再整单
    assert ordinary.status == RewardStatus.PENDING
    assert ordinary.audit_by == 'voter'
    assert task.status == TaskStatus.ARCHIVING  # 还有 2 条 archive pending_audit，不推进


@pytest.mark.asyncio
async def test_existing_auto_pass_endpoint_cannot_bypass_manual_archive_finance(db, monkeypatch):
    task, report, rewards = await prepare(db, monkeypatch)
    monkeypatch.setattr(admin, 'ensure_permission', MagicMock())
    db.add(RewardConfig(config_key='audit_policy', enabled=True, config=json.dumps({'auto_pass_max_fen': 10000})))
    await db.commit()
    result = await admin.run_auto_pass(db=db, current_user=actor('voter'))
    assert result.ok_count == 0
    assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards)
    assert task.status == TaskStatus.ARCHIVING
    admin.AuditDecisionEngine.evaluate.assert_not_awaited()
