import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Integer, MetaData, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.base import Base
from app.models.identity import UserDB
from app.models.knowledge import ArchiveReviewDecision, KnowledgeLike, KnowledgeLikeEvent, KnowledgePublication, KnowledgeSettlement, KnowledgeDiscussion
from app.models.task import ArchiveReport, Task, TaskStatus
from app.models.wxpay import RewardConfig, RewardRecord, RewardStatus
from app.modules.admin.api.knowledge import router
from app.modules.admin.services import knowledge_service as service
from app.modules.admin.services import knowledge_settlement as settlement
from app.modules.tasks.api.archive_report import router as review_router
from app.modules.tasks.services.archive_report_service import ArchiveReportService, validate_knowledge_report
from app.modules.tasks.services.knowledge_review import (
    blind_review_actor, own_review_history, projected_report, project_task_list, raw_access_guard, review_detail,
)


class AsyncDb:
    def __init__(self, session):
        self.session = session

    async def execute(self, query):
        return self.session.execute(query)

    async def get(self, model, identity):
        return self.session.get(model, identity)

    def add(self, model):
        self.session.add(model)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, model):
        self.session.refresh(model)


@pytest.fixture
def db(monkeypatch):
    from app.modules.tasks.services import archive_review_dispatch

    monkeypatch.setattr(archive_review_dispatch, 'archive_reviewer_ids', lambda: set())
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    metadata = MetaData()
    names = ['companies', 'departments', 'users', 'tasks', 'archive_reports', 'reward_record', 'reward_config', 'transfer_bill', 'task_operation_logs',
             'knowledge_publications', 'knowledge_question_likes', 'knowledge_like_events',
             'archive_review_decisions', 'knowledge_settlements', 'knowledge_discussions']
    for name in names:
        Base.metadata.tables[name].to_metadata(metadata)
    metadata.tables['users'].c.job_level.type = Integer()
    metadata.tables['task_operation_logs'].c.id.type = Integer()
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        for user_id, name in [('alice', '提单甲'), ('bob', '接单乙'), ('reviewer', '审核丙'), ('voter', '点赞丁')]:
            session.add(UserDB(id=user_id, username=f'{user_id}-account', name=name, status='active', password_hash='test'))
        session.add(RewardConfig(config_key='task_archive', enabled=True,
            config=json.dumps({'reporter_fen': 200, 'assignee_fen': 300, 'reviewer_fen': 100})))
        session.commit()
        monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 11, 20))
        yield AsyncDb(session)
    engine.dispose()


def actor(user_id):
    return {'id': user_id, 'username': f'{user_id}-account', 'permissions': []}


def seed(db, task_id=1, questioner='alice', answerer='bob', created=None, archived=None):
    task = Task(id=task_id, title='充电故障', description='现场无法充电', created_by=questioner,
                assigned_to=answerer, status=TaskStatus.ARCHIVED, created_at=created or datetime(2026, 11, 1),
                archived_at=archived or datetime(2026, 11, 10), archived_by='reviewer', project_name='秘密项目')
    report = ArchiveReport(id=task_id, task_id=task_id, content='## 解决方案\n检查供电', version=2,
                           created_by=answerer, reviewer='reviewer', submit_status='approved', revision=1,
                           review_comment='具有复用价值', value_point='减少排查时间')
    publication = KnowledgePublication(task_id=task_id, report_id=task_id, questioner_id=questioner,
        answerer_id=answerer, title=task.title, problem=task.description, content=report.content,
        review_comment=report.review_comment, value_point=report.value_point,
        first_approved_at=archived or datetime(2026, 11, 10), active=True)
    db.session.add_all([task, report, publication])
    db.session.commit()
    return task, report


@pytest.mark.asyncio
async def test_combines_roles_once_and_excludes_history_and_other_month(db):
    seed(db, 1)
    seed(db, 2, 'bob', 'voter')
    seed(db, 3, created=datetime(2026, 7, 31, 15, 59))
    seed(db, 4, created=datetime(2026, 11, 30, 16), archived=datetime(2026, 12, 1))
    items = await service.rankings(db, '2026-11')
    assert items[0]['user_id'] == 'bob'
    assert (items[0]['question_count'], items[0]['answer_count'], items[0]['contribution_count']) == (1, 1, 2)
    assert len(items) == len({item['user_id'] for item in items}) == 3
    assert {item['estimated_reward_fen'] for item in items} == {10000}


@pytest.mark.asyncio
async def test_october_contributions_use_question_time_and_beijing_month(db, monkeypatch):
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 10, 16))
    seed(db, 1, created=datetime(2026, 9, 30, 16), archived=datetime(2026, 9, 30, 16))
    seed(db, 2, created=datetime(2026, 9, 30, 15, 59), archived=datetime(2026, 10, 10))
    seed(db, 3, created=datetime(2026, 10, 1), archived=datetime(2026, 10, 31, 16))
    overview = await service.overview(db, actor('voter'))
    assert overview['month'] == '2026-10'
    assert overview['months'] == ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12']
    assert overview['campaign_status'] == 'active'
    assert {entry['user_id'] for entry in overview['top_three']} == {'alice', 'bob'}
    assert all(entry['contribution_count'] == 2 for entry in overview['top_three'])
    questions = await service.question_items(db, actor('voter'), '2026-10')
    assert [item['task_id'] for item in questions['items']] == [3, 1]
    september = await service.question_items(db, actor('voter'), '2026-09')
    assert [item['task_id'] for item in september['items']] == [2]
    november = await service.question_items(db, actor('voter'), '2026-11')
    assert november['items'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('month', ['2026-08', '2026-09', '2026-10'])
async def test_historical_question_published_in_october_counts_in_question_month(db, monkeypatch, month):
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 10, 16))
    created = datetime.strptime(f'{month}-01', '%Y-%m-%d')
    task, report = seed(db, created=created, archived=datetime(2026, 10, 15))
    db.session.delete(await db.get(KnowledgePublication, task.id))
    await db.flush()
    await service.publish_report(db, task, report)
    publication = await db.get(KnowledgePublication, task.id)
    assert publication.first_approved_at == datetime(2026, 10, 16)
    assert publication.active is True
    entries = await service.rankings(db, month)
    assert {entry['user_id'] for entry in entries} == {'alice', 'bob'}
    assert all(entry['contribution_count'] == 1 for entry in entries)
    assert all(entry['last_contribution_at'] == service.iso(created) for entry in entries)
    questions = await service.question_items(db, actor('voter'), month)
    assert [item['task_id'] for item in questions['items']] == [task.id]
    assert questions['items'][0]['created_at'] == service.iso(created)
    assert questions['items'][0]['archived_at'] == service.iso(datetime(2026, 10, 16))
    for user_id, role in [('alice', 'questioner'), ('bob', 'answerer')]:
        result = await service.contributor_questions(db, actor('voter'), user_id, month, role)
        assert result['total'] == 1
        assert result['months'] == list(service.CAMPAIGN_MONTHS)
    if month != '2026-10':
        assert (await service.question_items(db, actor('voter'), '2026-10'))['total'] == 0
        assert await service.rankings(db, '2026-10') == []
        with pytest.raises(HTTPException) as missing:
            await service.contributor_questions(db, actor('voter'), 'alice', '2026-10')
        assert missing.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('created,month', [
    (datetime(2026, 7, 31, 15, 59), None),
    (datetime(2026, 7, 31, 16), '2026-08'),
    (datetime(2026, 8, 31, 15, 59), '2026-08'),
    (datetime(2026, 8, 31, 16), '2026-09'),
    (datetime(2026, 9, 30, 16), '2026-10'),
])
async def test_question_month_boundaries_use_beijing_creation_time(db, created, month):
    seed(db, created=created, archived=datetime(2026, 10, 19))
    all_questions = await service.question_items(db, actor('voter'))
    assert all_questions['total'] == int(month is not None)
    for candidate in ('2026-08', '2026-09', '2026-10'):
        questions = await service.question_items(db, actor('voter'), candidate)
        assert questions['total'] == int(candidate == month)


@pytest.mark.asyncio
@pytest.mark.parametrize('sort', ['created_at', 'archived_at', 'likes'])
async def test_time_sort_uses_question_time_instead_of_publication_time(db, sort):
    seed(db, 1, created=datetime(2026, 8, 1), archived=datetime(2026, 10, 19))
    seed(db, 2, created=datetime(2026, 9, 1), archived=datetime(2026, 10, 10))
    result = await service.question_items(db, actor('voter'), sort=sort)
    assert [item['task_id'] for item in result['items']] == [2, 1]


@pytest.mark.asyncio
async def test_equal_contribution_and_like_counts_use_question_time_as_final_tiebreaker(db):
    seed(db, 1, created=datetime(2026, 8, 1), archived=datetime(2026, 10, 19))
    seed(db, 2, 'voter', 'reviewer', created=datetime(2026, 8, 2), archived=datetime(2026, 10, 10))
    result = await service.rankings(db, '2026-08')
    assert {entry['user_id'] for entry in result[:2]} == {'alice', 'bob'}


@pytest.mark.asyncio
async def test_like_review_statistics_follow_question_month_without_moving_other_snapshots(db, monkeypatch):
    seed(db, created=datetime(2026, 8, 1), archived=datetime(2026, 10, 19))
    db.add(KnowledgeLike(task_id=1, user_id='voter', active=True, risk_status='pending',
                         updated_at=datetime(2026, 10, 20)))
    await db.commit()
    monkeypatch.setattr(settlement, 'utcnow', lambda: datetime(2026, 11, 20))
    await settlement.freeze_month(db, actor('reviewer'), '2026-08', '核对提问月份')
    await settlement.freeze_month(db, actor('reviewer'), '2026-10', '核对公开月份')
    assert (await settlement.risk_items(db, '2026-08'))['total'] == 1
    assert (await settlement.risk_items(db, '2026-10'))['total'] == 0
    with pytest.raises(HTTPException) as pending:
        await settlement.review_snapshot(db, actor('reviewer'), '2026-08', True, '核验')
    assert pending.value.status_code == 409
    await settlement.mark_like_risk(db, actor('reviewer'), 1, 'voter', 'invalid', '异常点赞')
    assert (await db.get(KnowledgeSettlement, '2026-08')).status == 'stale'
    assert (await db.get(KnowledgeSettlement, '2026-10')).status == 'reviewing'
    assert db.session.scalars(select(RewardRecord)).all() == []


@pytest.mark.asyncio
async def test_likes_idempotent_cancel_and_historical_cutoff(db, monkeypatch):
    seed(db)
    await service.set_like(db, actor('voter'), 1, True)
    await service.set_like(db, actor('voter'), 1, True)
    assert len(db.session.scalars(select(KnowledgeLikeEvent)).all()) == 1
    cutoff = datetime(2026, 11, 30, 16)
    monkeypatch.setattr(service, 'utcnow', lambda: datetime(2026, 12, 2))
    await service.set_like(db, actor('voter'), 1, False)
    assert not await service.like_state(db)
    assert len(await service.like_state(db, cutoff)) == 1
    for user_id in ('alice', 'bob', 'reviewer'):
        with pytest.raises(HTTPException) as error:
            await service.set_like(db, actor(user_id), 1, True)
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_same_count_orders_by_received_likes_not_votes_cast(db):
    seed(db, 1, 'alice', 'bob')
    seed(db, 2, 'voter', 'reviewer')
    await service.set_like(db, actor('alice'), 2, True)
    entries = await service.rankings(db, '2026-11')
    assert {entry['user_id'] for entry in entries[:2]} == {'voter', 'reviewer'}
    assert entries[0]['valid_like_count'] == 1
    assert next(entry for entry in entries if entry['user_id'] == 'alice')['valid_like_count'] == 0


@pytest.mark.asyncio
async def test_public_detail_excludes_reviewer_and_unpublished_report(db):
    task, report = seed(db)
    detail = (await service.question_items(db, actor('voter'), task_id=1))['items'][0]
    assert detail['questioner']['name'] == '提单甲'
    assert detail['answerer']['name'] == '接单乙'
    assert '审核丙' not in str(detail) and 'reviewer' not in str(detail)
    report.submit_status, task.status = 'submitted', TaskStatus.ARCHIVING
    db.session.commit()
    with pytest.raises(HTTPException) as error:
        await service.question_items(db, actor('voter'), task_id=1)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_review_is_anonymous_and_only_assigned_reviewer(db):
    task, report = seed(db)
    report.submit_status = 'submitted'
    report.content = '提单甲、接单乙、审核丙在秘密项目，电话13812345678 [附件](https://secret.example/a)'
    db.session.commit()
    detail = await review_detail(db, actor('reviewer'), 1)
    for secret in ('提单甲', '接单乙', '审核丙', '秘密项目', '13812345678', 'https://'):
        assert secret not in str(detail)
    with pytest.raises(HTTPException):
        await review_detail(db, actor('voter'), 1)
    request = MagicMock(path_params={'task_id': 1})
    request.url.path = '/api/tasks/1/comments'
    with pytest.raises(HTTPException) as error:
        await raw_access_guard(request, db, actor('reviewer'))
    assert error.value.detail['code'] == 'BLIND_REVIEW_REQUIRED'


@pytest.mark.asyncio
async def test_rejection_private_and_history_survives_resubmission(db):
    task, report = seed(db)
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    db.session.commit()
    await ArchiveReportService.reject(db, 1, '请补充验证结果', 'reviewer', 1)
    await db.commit()
    assert len(db.session.scalars(select(ArchiveReviewDecision)).all()) == 1
    for user_id in ('alice', 'bob', 'reviewer'):
        assert (await projected_report(db, report, actor(user_id))).review_comment == '请补充验证结果'
    assert (await projected_report(db, report, actor('voter'))).review_comment is None
    report.version += 1
    report.submit_status, report.review_comment = 'submitted', None
    await db.commit()
    decision = db.session.scalars(select(ArchiveReviewDecision)).one()
    assert decision.review_comment == '请补充验证结果'


@pytest.mark.asyncio
async def test_approve_requires_current_revision_and_queues_finance_without_publication(db):
    task, report = seed(db)
    db.session.delete(await db.get(KnowledgePublication, 1))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await ArchiveReportService.approve(db, 1, 'reviewer', '意见', '价值', 99)
    assert error.value.status_code == 409
    await ArchiveReportService.approve(db, 1, 'reviewer', '通过意见', '价值点', 1)
    await db.commit()
    assert task.status == TaskStatus.ARCHIVING
    assert task.archived_at is None
    assert await db.get(KnowledgePublication, 1) is None
    rewards = db.session.scalars(select(RewardRecord)).all()
    assert len(rewards) == 3
    assert {reward.role for reward in rewards} == {'reporter', 'assignee', 'reviewer'}
    assert all(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards)
    assert all(reward.expire_at is None for reward in rewards)
    with pytest.raises(HTTPException):
        await ArchiveReportService.approve(db, 1, 'reviewer', '通过意见', '价值点', 1)
    assert len(db.session.scalars(select(ArchiveReviewDecision)).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['approved', 'rejected'])
async def test_service_rechecks_reviewer_assignment_and_participation_under_lock(db, outcome):
    task, report = seed(db)
    task.status, report.submit_status, report.reviewer = TaskStatus.ARCHIVING, 'submitted', 'voter'
    await db.commit()
    async def decide(operator):
        if outcome == 'approved':
            return await ArchiveReportService.approve(db, 1, operator, '意见', '价值点', 1)
        return await ArchiveReportService.reject(db, 1, '驳回意见', operator, 1)
    with pytest.raises(HTTPException) as changed:
        await decide('reviewer')
    assert changed.value.status_code == 403
    report.reviewer = 'alice-account'
    await db.commit()
    with pytest.raises(HTTPException) as self_review:
        await decide('alice')
    assert self_review.value.status_code == 403
    assert report.submit_status == 'submitted'
    assert not db.session.scalars(select(ArchiveReviewDecision)).all()


@pytest.mark.asyncio
async def test_republication_keeps_first_contributors_and_month(db):
    task, report = seed(db)
    original = await db.get(KnowledgePublication, 1)
    first_approval = original.first_approved_at
    task.created_by, report.created_by, report.content = 'voter', 'alice', '更新后的解答'
    await db.commit()
    await service.publish_report(db, task, report, datetime(2026, 12, 3))
    await db.commit()
    assert (original.questioner_id, original.answerer_id) == ('alice', 'bob')
    assert original.first_approved_at == first_approval
    assert original.content == '更新后的解答'
    assert not await service.rankings(db, '2026-12')
    assert {entry['user_id'] for entry in await service.rankings(db, '2026-11')} == {'alice', 'bob'}


@pytest.mark.asyncio
async def test_review_history_entry_survives_reassignment_and_hides_names(db):
    task, report = seed(db)
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    await ArchiveReportService.reject(db, 1, '审核丙：请补充验证', 'reviewer', 1)
    report.reviewer = 'voter'
    await db.commit()
    assert (await service.overview(db, actor('reviewer')))['can_review']
    history = await own_review_history(db, actor('reviewer'))
    assert history['total'] == 1
    assert '审核丙' not in history['items'][0]['review_comment']


@pytest.mark.asyncio
async def test_related_ticket_projection_hides_pending_identity(db):
    task, report = seed(db)
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    task.title = '提单甲的充电故障'
    await db.commit()
    result = await project_task_list(db, {'items': [], 'related_tickets': [task]}, actor('reviewer'))
    related = result['related_tickets'][0]
    assert '提单甲' not in related['title']
    assert not related['created_by'] and not related['assigned_to']


@pytest.mark.asyncio
async def test_non_task_integration_routes_do_not_require_user_token():
    from starlette.requests import Request
    request = Request({'type': 'http', 'path': '/api/tasks/robot-alarm-notify', 'headers': [],
                       'query_string': b'', 'path_params': {}})
    assert await blind_review_actor(request) is None


@pytest.mark.asyncio
async def test_monthly_snapshot_risk_gate_and_grant_idempotency(db, monkeypatch):
    seed(db)
    with pytest.raises(HTTPException):
        await settlement.freeze_month(db, actor('reviewer'), '2026-11', '核验')
    monkeypatch.setattr(settlement, 'utcnow', lambda: datetime(2026, 12, 2))
    await settlement.freeze_month(db, actor('reviewer'), '2026-11', '冻结11月')
    with pytest.raises(HTTPException):
        await settlement.grant_month(db, actor('reviewer'), '2026-11')
    await settlement.review_snapshot(db, actor('reviewer'), '2026-11', True, '未发现异常')
    await settlement.grant_month(db, actor('reviewer'), '2026-11')
    await settlement.grant_month(db, actor('reviewer'), '2026-11')
    rewards = db.session.scalars(select(RewardRecord)).all()
    assert len(rewards) == 2 and sum(reward.amount for reward in rewards) == 20000
    with pytest.raises(HTTPException):
        await settlement.freeze_month(db, actor('reviewer'), '2026-11', '不能覆盖已发榜')


@pytest.mark.asyncio
async def test_risk_interface_returns_no_problem_or_review_content(db):
    seed(db)
    await service.set_like(db, actor('voter'), 1, True)
    result = await settlement.risk_items(db, '2026-11')
    assert result['items'][0]['user_like_count'] == 1
    for content in ('现场无法充电', '检查供电', '具有复用价值', '减少排查时间'):
        assert content not in str(result)
    await settlement.mark_like_risk(db, actor('reviewer'), 1, 'voter', 'invalid', '集中异常点赞')
    assert not await service.like_state(db)


def test_pending_boundary_blocks_grant_without_inventing_prize_rules():
    entries = [{'contribution_count': 1, 'valid_like_count': 0} for _ in range(4)]
    assert settlement.unresolved_boundary(entries)
    assert settlement.unresolved_boundary([{'has_dual_role': True}])


def test_report_requires_sections_with_body_and_accepts_numbered_headings():
    headings = ('问题概述', '问题场景', '复现条件', '原因分析', '解决方案', '验证结果', '适用范围', '遗留问题')
    with pytest.raises(HTTPException) as empty:
        validate_knowledge_report('\n\n'.join(f'## {heading}' for heading in headings))
    assert empty.value.status_code == 422
    validate_knowledge_report('\n\n'.join(f'## {index + 1}. {heading}\n具体内容' for index, heading in enumerate(headings)))


@pytest.mark.asyncio
async def test_existing_websocket_connection_stops_receiving_after_review_assignment(monkeypatch):
    from app.modules.tasks.api import ws as websocket_api
    websocket = AsyncMock()
    connection = websocket_api.WsConnection(websocket, 'reviewer-account', '审核人', 1)
    monkeypatch.setattr(websocket_api, 'restricted_review_room', lambda task_id, user: True)
    await connection.send({'type': 'comment', 'created_by': 'alice'})
    websocket.send_text.assert_not_awaited()
    websocket.close.assert_awaited_once_with(code=4403)


def test_api_open_to_logged_users_but_management_and_review_private(db):
    seed(db)
    app = FastAPI()
    app.include_router(router)
    app.include_router(review_router, prefix='/archive-report')
    async def database():
        yield db
    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('voter')
    with TestClient(app) as client:
        assert client.get('/knowledge/overview').status_code == 200
        assert client.get('/knowledge/questions/1').status_code == 200
        assert client.get('/knowledge/risk?month=2026-11').status_code == 403
        assert client.get('/archive-report/archive-report/reviews/1').status_code == 404
        assert client.post('/archive-report/archive-report/archive-reports/1/approve', json={}).status_code == 422
        assert client.get('/knowledge/rankings?month=2026-10').status_code == 200
        for month in ('2026-08', '2026-09'):
            assert client.get(f'/knowledge/overview?month={month}').status_code == 200
            assert client.get(f'/knowledge/rankings?month={month}').status_code == 200
            assert client.get(f'/knowledge/questions?month={month}&sort=created_at').status_code == 200
        assert client.get('/knowledge/rankings?month=2026-07').status_code == 422
        assert client.get('/knowledge/questions?sort=archived_at').status_code == 200
        assert client.get('/knowledge/questions?sort=invalid').status_code == 422


def test_migration_adds_column_and_is_repeatable():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / 'alembic/versions/c9d2e5f8a104_knowledge_contribution.py'
    spec = importlib.util.spec_from_file_location('knowledge_migration_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE archive_reports (id INTEGER PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        from sqlalchemy import inspect
        assert 'value_point' in {column['name'] for column in inspect(connection).get_columns('archive_reports')}
        assert 'knowledge_publications' in inspect(connection).get_table_names()
    engine.dispose()
