"""MySQL integration coverage for standard task creation persistence.

The suite is deliberately opt-in: it runs only when TEST_DATABASE_URL points to
an isolated MySQL database.  It never falls back to the development DATABASE_URL.
"""
import os

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.task import Task, TaskComment, TaskPriority, TaskStatus, TaskType
from app.modules.tasks.schemas.ticket import TicketCreate
from app.modules.tasks.services.ticket_service import TicketService
from app.modules.tasks.api.task import get_db, router


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Set TEST_DATABASE_URL to an isolated MySQL database to run persistence tests.",
)


def _async_url(url: str) -> str:
    if url.startswith("mysql+pymysql://"):
        return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    return url


@pytest.fixture(scope="module")
def mysql_schema():
    """Create only task tables in the isolated test database and remove them afterwards."""
    sync_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(sync_engine, tables=[Task.__table__, TaskComment.__table__])
    yield sync_engine
    sync_engine.dispose()


@pytest_asyncio.fixture
async def db_session(mysql_schema):
    engine = create_async_engine(_async_url(TEST_DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_standard_task_persists_task_and_initial_comment(
    db_session, monkeypatch
):
    """TC-T01/TC-T02: verify the committed task and initial comment in MySQL."""
    notification = pytest.importorskip("unittest.mock").AsyncMock(return_value={"code": 200})

    monkeypatch.setattr(
        "app.modules.tasks.services.ticket_service.NotificationUtils.send_ticket_create_notification",
        notification,
    )
    monkeypatch.setattr(
        "app.modules.tasks.services.ticket_service.user_service.get_user_map",
        lambda: {"engineer-01": "工程师一号", "customer-01": "客户一号"},
    )

    payload = TicketCreate(
        title="标准建单数据库验证",
        description="验证任务主表与首条评论在同一业务动作中落库。",
        ticket_type=TaskType.PROBLEM,
        priority=TaskPriority.HIGH,
        assigned_to="engineer-01",
        customer="customer-01",
        project_name="自动化测试项目",
        project_id="autotest-project",
        tags=["autotest"],
    )

    task = await TicketService.create_ticket(
        db_session,
        payload,
        created_by="creator-01",
        comment_attachment_map={},
    )

    persisted_task = (
        await db_session.execute(select(Task).where(Task.id == task.id))
    ).scalar_one()
    comments = (
        await db_session.execute(
            select(TaskComment).where(TaskComment.task_id == task.id)
        )
    ).scalars().all()

    assert persisted_task.title == payload.title
    assert persisted_task.description == ""
    assert persisted_task.task_type == TaskType.PROBLEM
    assert persisted_task.created_by == "creator-01"
    assert persisted_task.assigned_to == "engineer-01"
    assert persisted_task.customer == "customer-01"
    assert persisted_task.project_name == "自动化测试项目"
    assert persisted_task.project_id == "autotest-project"
    assert persisted_task.status == TaskStatus.PENDING
    assert persisted_task.priority == TaskPriority.HIGH
    assert persisted_task.tags == ["autotest"]
    assert persisted_task.source == "manual"
    assert persisted_task.view_count == 0
    assert persisted_task.created_at is not None
    assert persisted_task.updated_at is not None
    assert persisted_task.reply_count == 1
    assert len(comments) == 1
    assert comments[0].content == payload.description
    assert comments[0].created_by == "creator-01"
    assert comments[0].is_public is True
    assert comments[0].attachments == []
    notification.assert_awaited_once_with(
        task.id,
        payload.title,
        payload.project_name,
        "creator-01",
        ["engineer-01", "customer-01"],
        None,
    )


@pytest.mark.asyncio
async def test_create_standard_task_rolls_back_when_initial_comment_cannot_be_added(
    db_session, monkeypatch
):
    """TC-T02/TC-T05: no task row may remain if the creation transaction fails."""
    original_add = db_session.add
    add_count = 0

    def fail_when_adding_initial_comment(entity):
        nonlocal add_count
        add_count += 1
        if isinstance(entity, TaskComment):
            raise RuntimeError("simulated comment insert failure")
        original_add(entity)

    monkeypatch.setattr(db_session, "add", fail_when_adding_initial_comment)
    payload = TicketCreate(
        title="应回滚的标准建单",
        description="首条评论插入失败时不得遗留任务。",
        ticket_type=TaskType.PROBLEM,
        priority=TaskPriority.MEDIUM,
        assigned_to="engineer-01",
    )

    with pytest.raises(RuntimeError, match="simulated comment insert failure"):
        await TicketService.create_ticket(
            db_session,
            payload,
            created_by="creator-rollback",
            comment_attachment_map={},
        )

    rows = (
        await db_session.execute(
            select(Task).where(Task.title == "应回滚的标准建单")
        )
    ).scalars().all()
    assert rows == []
    comments = (
        await db_session.execute(
            select(TaskComment).where(TaskComment.created_by == "creator-rollback")
        )
    ).scalars().all()
    assert comments == []


def test_create_standard_task_api_persists_complete_record(mysql_schema, monkeypatch):
    """TC-T01: validate Router → Service → MySQL without mocking persistence."""
    from unittest.mock import AsyncMock

    notification = AsyncMock(return_value={"code": 200})
    monkeypatch.setattr(
        "app.modules.tasks.services.ticket_service.NotificationUtils.send_ticket_create_notification",
        notification,
    )
    monkeypatch.setattr(
        "app.modules.tasks.services.ticket_service.user_service.get_user_map",
        lambda: {"engineer-e2e": "工程师端到端", "customer-e2e": "客户端到端"},
    )

    async_engine = create_async_engine(_async_url(TEST_DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    payload = {
        "title": "标准建单 API 到数据库验证",
        "description": "通过真实 API 入口验证主记录与初始评论。",
        "ticket_type": "problem",
        "priority": "urgent",
        "assigned_to": "engineer-e2e",
        "customer": "customer-e2e",
        "project_name": "端到端项目",
        "project_id": "e2e-project",
        "tags": ["e2e", "standard-create"],
        "metadata_info": {"channel": "api"},
    }

    with TestClient(app) as client:
        response = client.post("/api/tasks/", json=payload)
    async_engine.sync_engine.dispose()

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == payload["title"]
    assert body["ticket_type"] == payload["ticket_type"]
    assert body["priority"] == payload["priority"]
    assert body["status"] == "pending"
    assert body["customer"] == payload["customer"]
    assert body["project_name"] == payload["project_name"]
    assert body["project_id"] == payload["project_id"]
    assert body["tags"] == payload["tags"]
    assert body["metadata_info"] == payload["metadata_info"]

    with mysql_schema.connect() as connection:
        task = connection.execute(select(Task).where(Task.id == body["id"])).scalar_one()
        comments = connection.execute(
            select(TaskComment).where(TaskComment.task_id == body["id"])
        ).scalars().all()
    assert task.title == payload["title"]
    assert task.task_type == TaskType.PROBLEM
    assert task.priority == TaskPriority.URGENT
    assert task.customer == payload["customer"]
    assert task.project_name == payload["project_name"]
    assert task.project_id == payload["project_id"]
    assert task.tags == payload["tags"]
    assert task.metadata_info == payload["metadata_info"]
    assert len(comments) == 1
    assert comments[0].content == payload["description"]
