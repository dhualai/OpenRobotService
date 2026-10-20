import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.models.identity import UserDB
from app.models.knowledge import ArchiveReviewDecision, KnowledgeDiscussion
from app.models.task import TaskStatus
from app.modules.admin.api.knowledge import router
from app.modules.admin.services import knowledge_service as service
from app.modules.tasks.services import archive_review_dispatch
from backend.tests.test_knowledge import actor, db, seed


@pytest.mark.asyncio
async def test_designated_reviewer_sees_workbench_before_first_assignment(db, monkeypatch):
    monkeypatch.setattr(archive_review_dispatch, 'archive_reviewer_ids', lambda: {'reviewer-account'})
    result = await service.overview(db, actor('reviewer'))
    assert result['can_review'] is True
    assert result['reviewer_configured'] is True
    assert result['pending_review_count'] == 0
    assert result['top_three'] == []
    assert (await service.overview(db, actor('voter')))['can_review'] is False


@pytest.mark.asyncio
async def test_admin_can_open_workbench_without_receiving_other_reviewers_reports(db):
    from app.modules.tasks.services.knowledge_review import pending_reviews, review_detail

    task, report = seed(db)
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    admin = {**actor('voter'), 'permissions': ['admin']}
    assert (await service.overview(db, admin))['can_review'] is True
    assert (await pending_reviews(db, admin))['total'] == 0
    with pytest.raises(HTTPException) as denied:
        await review_detail(db, admin, report.id)
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_pending_review_count_is_personal_and_independent_of_activity_month(db):
    task, report = seed(db, created=datetime(2026, 10, 1))
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    result = await service.overview(db, actor('reviewer'), '2026-12')
    assert result['can_review'] is True
    assert result['pending_review_count'] == 1
    assert result['top_three'] == []
    assert (await service.overview(db, actor('voter')))['pending_review_count'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('reviewer_identity', ['reviewer-account', 'reviewer-openid'])
async def test_review_queue_history_and_permissions_use_same_normalized_identity(db, reviewer_identity):
    from app.modules.tasks.services.archive_report_service import require_assigned_reviewer
    from app.modules.tasks.services.knowledge_review import own_review_history, pending_reviews, raw_access_guard, review_detail

    task, report = seed(db)
    reviewer = await db.get(UserDB, 'reviewer')
    reviewer.wechat_openid = 'reviewer-openid'
    task.status, report.submit_status, report.reviewer = TaskStatus.ARCHIVING, 'submitted', reviewer_identity
    db.session.add(ArchiveReviewDecision(task_id=1, report_id=1, version=1, reviewer_id=reviewer_identity,
        outcome='rejected', review_comment='补充说明', content='旧报告', created_at=datetime(2026, 11, 1)))
    await db.commit()
    current_actor = actor('reviewer')
    assert (await service.overview(db, current_actor))['pending_review_count'] == 1
    assert (await pending_reviews(db, current_actor))['items'][0]['report_id'] == report.id
    assert (await own_review_history(db, current_actor))['items'][0]['review_comment'] == '补充说明'
    assert (await review_detail(db, current_actor, report.id))['report_id'] == report.id
    await require_assigned_reviewer(db, report, task, 'reviewer')
    request = MagicMock(path_params={'task_id': 1})
    request.url.path = '/api/tasks/1/comments'
    with pytest.raises(HTTPException) as blind:
        await raw_access_guard(request, db, current_actor)
    assert blind.value.detail['code'] == 'BLIND_REVIEW_REQUIRED'
    with pytest.raises(HTTPException) as denied:
        await require_assigned_reviewer(db, report, task, 'voter')
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException):
        await review_detail(db, actor('voter'), report.id)
    report.submit_status, task.status = 'approved', TaskStatus.ARCHIVED
    await db.commit()
    assert (await service.question_items(db, current_actor, task_id=1))['items'][0]['can_like'] is False
    with pytest.raises(HTTPException) as voting:
        await service.set_like(db, current_actor, 1, True)
    assert voting.value.status_code == 403


@pytest.mark.asyncio
async def test_contributor_filters_use_first_published_attribution_and_public_tasks_only(db):
    seed(db, 1)
    seed(db, 2, questioner='bob', answerer='alice')
    seed(db, 3, questioner='alice', created=datetime(2026, 9, 30, 15, 59))
    task, report = seed(db, 4, questioner='alice')
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'submitted'
    await db.commit()
    result = await service.contributor_questions(db, actor('voter'), 'alice', '2026-11')
    assert result['user']['user_id'] == 'alice'
    assert (result['question_count'], result['answer_count']) == (1, 1)
    assert {item['task_id'] for item in result['items']} == {1, 2}
    questions = await service.contributor_questions(db, actor('voter'), 'alice', '2026-11', 'questioner')
    answers = await service.contributor_questions(db, actor('voter'), 'alice', '2026-11', 'answerer')
    assert [item['task_id'] for item in questions['items']] == [1]
    assert [item['task_id'] for item in answers['items']] == [2]
    assert 'project_name' not in questions['items'][0]
    assert 'reviewer' not in questions['items'][0]


@pytest.mark.asyncio
async def test_contributor_list_paginates_and_empty_role_remains_valid(db):
    seed(db, 1)
    seed(db, 2)
    result = await service.contributor_questions(db, actor('voter'), 'alice', '2026-11', page=2, size=1)
    assert result['total'] == 2
    assert len(result['items']) == 1
    result = await service.contributor_questions(db, actor('voter'), 'alice', '2026-11', 'answerer')
    assert result['items'] == []
    with pytest.raises(HTTPException) as missing:
        await service.contributor_questions(db, actor('voter'), 'voter', '2026-11')
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_discussion_public_access_and_pagination_do_not_change_rankings(db):
    seed(db)
    before = await service.rankings(db, '2026-11')
    await service.add_discussion(db, actor('alice'), 1, '  我补充了排查经验  ')
    await service.add_discussion(db, actor('voter'), 1, '请问如何验证恢复结果？')
    result = await service.discussion_items(db, actor('bob'), 1, page=1, size=1)
    assert result['total'] == 2
    assert result['items'][0]['content'] == '请问如何验证恢复结果？'
    assert result['items'][0]['author']['user_id'] == 'voter'
    assert (await service.discussion_items(db, actor('bob'), 1, page=2, size=1))['items'][0]['content'] == '我补充了排查经验'
    assert await service.rankings(db, '2026-11') == before


@pytest.mark.asyncio
async def test_discussions_hide_reviewer_author_and_review_identity_in_content(db):
    seed(db)
    reviewer = await db.get(UserDB, 'reviewer')
    reviewer.wechat_openid = 'wx-audit-private-id'
    await db.commit()
    row = await service.add_discussion(db, actor('reviewer'), 1, '审核丙在秘密项目复现了问题 wx-audit-private-id https://private.example/document')
    assert row['author'] is None
    for hidden in ('审核丙', 'reviewer', 'wx-audit-private-id', '秘密项目', 'private.example'):
        assert hidden not in str(row)
    ordinary = await service.add_discussion(db, actor('alice'), 1, '感谢审核丙，已经验证')
    assert ordinary['author']['user_id'] == 'alice'
    assert '审核丙' not in ordinary['content']


@pytest.mark.asyncio
async def test_discussions_are_not_exposed_for_unpublished_questions(db):
    task, report = seed(db)
    await service.add_discussion(db, actor('voter'), 1, '曾公开的知识讨论')
    task.status, report.submit_status = TaskStatus.ARCHIVING, 'rejected'
    await db.commit()
    for action in (lambda: service.discussion_items(db, actor('voter'), 1),
                   lambda: service.add_discussion(db, actor('voter'), 1, '新讨论')):
        with pytest.raises(HTTPException) as denied:
            await action()
        assert denied.value.status_code == 404
    assert len(db.session.scalars(select(KnowledgeDiscussion)).all()) == 1


def test_discussion_and_contributor_api_validate_input_and_authentication(db):
    seed(db)
    seed(db, 2, created=datetime(2026, 10, 1), archived=datetime(2026, 10, 15))
    seed(db, 3, created=datetime(2026, 8, 1), archived=datetime(2026, 10, 15))
    seed(db, 4, created=datetime(2026, 9, 1), archived=datetime(2026, 10, 15))
    app = FastAPI()
    app.include_router(router)

    async def database():
        yield db

    app.dependency_overrides[get_async_db] = database
    app.dependency_overrides[get_current_active_user_from_token] = lambda: actor('voter')
    with TestClient(app) as client:
        assert client.post('/knowledge/questions/1/discussions', json={'content': '  '}).status_code == 422
        assert client.post('/knowledge/questions/1/discussions', json={'content': '字' * 2001}).status_code == 422
        response = client.post('/knowledge/questions/1/discussions', json={'content': '公开讨论内容'})
        assert response.status_code == 201
        assert response.json()['author']['user_id'] == 'voter'
        assert client.get('/knowledge/questions/1/discussions').json()['total'] == 1
        assert client.get('/knowledge/questions/999/discussions').status_code == 404
        assert client.get('/knowledge/questions/1/discussions?page=0').status_code == 422
        assert client.get('/knowledge/contributors/alice/questions?month=2026-11').json()['total'] == 1
        assert client.get('/knowledge/contributors/alice/questions?role=private').status_code == 422
        assert client.get('/knowledge/contributors/alice/questions?month=2026-10').status_code == 200
        for month, task_id in [('2026-08', 3), ('2026-09', 4)]:
            result = client.get(f'/knowledge/contributors/alice/questions?month={month}').json()
            assert result['total'] == 1
            assert result['months'] == list(service.CAMPAIGN_MONTHS)
            assert result['items'][0]['task_id'] == task_id
            questions = client.get(f'/knowledge/questions?month={month}&sort=created_at').json()
            assert [item['task_id'] for item in questions['items']] == [task_id]
            rankings = client.get(f'/knowledge/rankings?month={month}').json()
            assert all(entry['contribution_count'] == 1 for entry in rankings['items'])
        assert client.get('/knowledge/contributors/alice/questions?month=2026-07').status_code == 422
        app.dependency_overrides.pop(get_current_active_user_from_token)
        assert client.get('/knowledge/questions/1/discussions').status_code == 401
        assert client.post('/knowledge/questions/1/discussions', json={'content': '未登录'}).status_code == 401
        assert client.get('/knowledge/contributors/alice/questions').status_code == 401


def test_discussion_migration_is_repeatable_and_keeps_existing_rows():
    path = Path(__file__).parents[1] / 'alembic/versions/d0e3f6a9b215_knowledge_discussions.py'
    spec = importlib.util.spec_from_file_location('knowledge_discussions_migration_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            connection.exec_driver_sql("INSERT INTO knowledge_discussions(task_id, author_id, content, created_at) VALUES (1, 'alice', 'keep', '2026-11-01')")
            migration.upgrade()
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM knowledge_discussions').scalar_one() == 1
            assert {index['name'] for index in inspect(connection).get_indexes('knowledge_discussions')} == {
                'ix_knowledge_discussions_task_id', 'ix_knowledge_discussions_author_id', 'ix_knowledge_discussions_created_at'}
            migration.downgrade()
            assert not inspect(connection).has_table('knowledge_discussions')
    engine.dispose()
