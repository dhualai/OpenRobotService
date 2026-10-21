from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.knowledge import ArchiveReviewDecision, KnowledgePublication
from app.models.task import TaskStatus
from app.models.wxpay import RewardRecord, RewardStatus
from app.modules.tasks.api import archive_report as review_api
from app.modules.tasks.services import archive_review_dispatch
from app.modules.tasks.services.archive_report_service import ArchiveReportService
from app.modules.tasks.services.knowledge_review import pending_reviews, projected_report
from backend.tests.test_knowledge import actor, db, seed


@pytest.fixture(autouse=True)
def local_user_lookup(monkeypatch):
    monkeypatch.setattr('app.core.user_identity.lookup_user', lambda raw: None)


async def draft_report(db):
    task, report = seed(db)
    db.session.delete(await db.get(KnowledgePublication, task.id))
    task.status, report.submit_status, report.reviewer = TaskStatus.ARCHIVING, 'draft', None
    task.archived_by, task.archived_at = None, None
    await db.commit()
    return task, report


def valid_content():
    headings = ('问题概述', '问题场景', '复现条件', '原因分析', '解决方案', '验证结果', '适用范围', '遗留问题')
    return '\n\n'.join(f'## {heading}\n具体内容' for heading in headings)


@pytest.mark.asyncio
@pytest.mark.parametrize('participant', ['alice', 'bob'])
@pytest.mark.parametrize('identity_suffix', ['', '-account', '-openid'])
@pytest.mark.parametrize('outcome', ['approved', 'rejected'])
async def test_dispatched_participant_can_review_and_keeps_finance_flow(
    db, monkeypatch, participant, identity_suffix, outcome,
):
    task, report = await draft_report(db)
    reviewer = participant + identity_suffix
    user = await db.get(UserDB, participant)
    user.wechat_openid = participant + '-openid'
    await db.commit()
    assignment = SimpleNamespace(reviewer_id=reviewer)
    dispatch = AsyncMock(return_value=assignment)
    monkeypatch.setattr(archive_review_dispatch, 'dispatch_archive_reviewer', dispatch)

    submitted, actual_assignment = await ArchiveReportService.submit(db, report.id, valid_content(), 1, 'bob')
    await db.commit()

    dispatch.assert_awaited_once()
    assert actual_assignment is assignment
    assert submitted.reviewer == reviewer
    assert submitted.submit_status == 'submitted'
    assert submitted.revision == 2
    assert submitted.review_comment is None
    assert submitted.value_point is None
    assert assignment._log_detail['reviewer'] == reviewer
    assert (await pending_reviews(db, actor(participant)))['total'] == 1
    assert (await projected_report(db, report, actor(participant))).can_review is True

    if outcome == 'approved':
        await ArchiveReportService.approve(db, report.id, participant, '通过意见', '可复用价值', 2)
    else:
        await ArchiveReportService.reject(db, report.id, '请补充验证', participant, 2)
    await db.commit()

    assert report.submit_status == outcome
    assert report.revision == 3
    assert task.status == TaskStatus.ARCHIVING
    assert task.archived_at is None
    assert await db.get(KnowledgePublication, task.id) is None
    decision = db.session.scalars(select(ArchiveReviewDecision)).one()
    assert decision.reviewer_id == participant
    assert decision.outcome == outcome
    rewards = db.session.scalars(select(RewardRecord)).all()
    if outcome == 'approved':
        assert len(rewards) == 3
        assert {reward.role for reward in rewards} == {'reporter', 'assignee', 'reviewer'}
        assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards)
        assert all(reward.expire_at is None for reward in rewards)
    else:
        assert rewards == []


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_reviewer', [False, True])
async def test_unsuccessful_dispatch_cannot_be_bypassed_with_manual_reviewer(db, monkeypatch, missing_reviewer):
    task, report = await draft_report(db)
    dispatch = AsyncMock(return_value=SimpleNamespace(reviewer_id=None))
    if not missing_reviewer:
        failure = RuntimeError('没有可用审核人')
        failure.code = 'no_reviewers'
        dispatch.side_effect = failure
    monkeypatch.setattr(archive_review_dispatch, 'dispatch_archive_reviewer', dispatch)

    with pytest.raises(HTTPException) as error:
        await ArchiveReportService.submit(db, report.id, valid_content(), 1, 'bob', reviewer_id='alice')

    assert error.value.status_code == 400
    assert report.submit_status == 'draft'
    assert report.reviewer is None
    assert report.revision == 1
    assert report.content == '## 解决方案\n检查供电'
    assert task.status == TaskStatus.ARCHIVING


@pytest.mark.parametrize('participant', ['alice', 'bob'])
@pytest.mark.parametrize('outcome', ['approve', 'reject'])
def test_review_api_accepts_assigned_participant_but_not_other_users(db, monkeypatch, participant, outcome):
    task, report = seed(db)
    db.session.delete(db.session.get(KnowledgePublication, task.id))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    task.archived_by, task.archived_at = None, None
    report.reviewer = participant + '-account'
    db.session.commit()
    monkeypatch.setattr(review_api, '_add_system_comment', AsyncMock())
    monkeypatch.setattr(review_api, 'ws_broadcast_task_updated', AsyncMock())
    app = FastAPI()
    app.include_router(review_api.router)

    async def database():
        yield db

    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('voter')
    body = {'review_comment': '审核意见', 'value_point': '可复用价值', 'revision': 1}
    path = f'/archive-report/archive-reports/{report.id}/{outcome}'
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 403
        app.dependency_overrides[get_current_active_user_from_token] = lambda: actor(participant)
        stale = client.post(path, json={**body, 'revision': 99})
        assert stale.status_code == 409
        assert client.post(path, json={**body, 'review_comment': ' '}).status_code == 422
        if outcome == 'approve':
            assert client.post(path, json={**body, 'value_point': ' '}).status_code == 422
        assert report.submit_status == 'submitted'
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()['submit_status'] == ('approved' if outcome == 'approve' else 'rejected')
        assert client.post(path, json={**body, 'revision': report.revision}).status_code == 400

    assert task.status == TaskStatus.ARCHIVING
    assert db.session.get(KnowledgePublication, task.id) is None
    decision = db.session.scalars(select(ArchiveReviewDecision)).one()
    assert decision.reviewer_id == participant
