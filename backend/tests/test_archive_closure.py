import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgePublication
from app.models.task import TaskStatus
from app.models.wxpay import RewardConfig, RewardRecord, RewardStatus
from app.modules.admin.services import knowledge_service
from app.modules.tasks.services.archive_report_service import ArchiveReportService
from app.modules.wxpay import archive_finance
from backend.tests.test_knowledge import actor, db, seed


async def submitted_report(db, created=None):
    task, report = seed(db, created=created or datetime(2026, 10, 1), archived=datetime(2026, 10, 10))
    db.session.delete(await db.get(KnowledgePublication, task.id))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    task.archived_at, task.archived_by = None, None
    db.add(RewardConfig(config_key='audit_policy', enabled=True, config=json.dumps({'auto_pass_max_fen': 10000})))
    await db.commit()
    return task, report


@pytest.mark.asyncio
@pytest.mark.parametrize('month', ['2026-08', '2026-09', '2026-10'])
async def test_automatic_finance_approval_finishes_archive_in_question_month(db, monkeypatch, month):
    task, report = await submitted_report(db, datetime.strptime(f'{month}-01', '%Y-%m-%d'))
    monkeypatch.setattr(knowledge_service, 'utcnow', lambda: datetime(2026, 10, 19))
    decision = AsyncMock(return_value=True)
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'evaluate', decision)

    await ArchiveReportService.approve(db, report.id, 'reviewer', '通过意见', '可复用价值', 1)
    await db.commit()

    assert decision.await_count == 3
    assert task.status == TaskStatus.ARCHIVED
    assert report.submit_status == 'approved'
    rewards = db.session.scalars(select(RewardRecord)).all()
    assert len(rewards) == 3
    assert all(reward.status == RewardStatus.PENDING and reward.audit_by == 'AUTO' for reward in rewards)
    publication = await db.get(KnowledgePublication, task.id)
    assert publication.active is True
    assert publication.report_id == report.id
    assert publication.first_approved_at == datetime(2026, 10, 19)
    questions = await knowledge_service.question_items(db, actor('voter'), month)
    assert [item['task_id'] for item in questions['items']] == [task.id]
    assert (await knowledge_service.question_items(db, actor('voter'), '2026-11'))['total'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('month', ['2026-08', '2026-09', '2026-10'])
async def test_mixed_finance_results_publish_only_after_existing_manual_approval(db, monkeypatch, month):
    task, report = await submitted_report(db, datetime.strptime(f'{month}-01', '%Y-%m-%d'))
    monkeypatch.setattr(knowledge_service, 'utcnow', lambda: datetime(2026, 10, 19))
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'evaluate', AsyncMock(side_effect=[True, False, True]))
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'reason', AsyncMock(return_value='原财务策略需人工审核'))

    await ArchiveReportService.approve(db, report.id, 'reviewer', '通过意见', '可复用价值', 1)
    await db.commit()
    assert task.status == TaskStatus.ARCHIVING
    assert await db.get(KnowledgePublication, task.id) is None
    assert (await knowledge_service.question_items(db, actor('voter'), month))['total'] == 0
    rewards = db.session.scalars(select(RewardRecord)).all()
    pending = [reward for reward in rewards if reward.status == RewardStatus.PENDING_AUDIT]
    auto = [reward for reward in rewards if reward.status == RewardStatus.PENDING]
    assert len(pending) == 1
    assert len(auto) == 2

    processed = await archive_finance.audit_archive_rewards(db, [pending[0].id], 'voter', True, '按原流程审核通过')
    await db.commit()
    assert len(processed) == 1
    assert all(reward.audit_by == 'AUTO' for reward in auto)
    assert pending[0].audit_by == 'voter'
    assert task.status == TaskStatus.ARCHIVED
    assert (await db.get(KnowledgePublication, task.id)).active is True
    assert [item['task_id'] for item in (await knowledge_service.question_items(db, actor('voter'), month))['items']] == [task.id]


@pytest.mark.asyncio
async def test_pre_august_task_can_finish_finance_but_is_excluded_by_activity_rules(db, monkeypatch):
    task, report = await submitted_report(db)
    task.created_at = datetime(2026, 7, 31, 15, 59)
    await db.commit()
    monkeypatch.setattr(knowledge_service, 'utcnow', lambda: datetime(2026, 10, 19))
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'evaluate', AsyncMock(return_value=False))
    monkeypatch.setattr(archive_finance.AuditDecisionEngine, 'reason', AsyncMock(return_value='需人工审核'))
    await ArchiveReportService.approve(db, report.id, 'reviewer', '通过意见', '可复用价值', 1)
    await db.commit()
    rewards = db.session.scalars(select(RewardRecord)).all()
    await archive_finance.audit_archive_rewards(db, [reward.id for reward in rewards], 'voter', True)
    await db.commit()
    assert task.status == TaskStatus.ARCHIVED
    assert await db.get(KnowledgePublication, task.id) is None
    assert (await knowledge_service.question_items(db, actor('voter'), '2026-10'))['total'] == 0
