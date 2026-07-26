"""MySQL integration coverage for PATCH /api/tasks/{task_id}/status persistence.

The suite is deliberately opt-in: it runs only when TEST_DATABASE_URL points to
an isolated MySQL database.  It never falls back to the development DATABASE_URL.

Business rules from code (app/models/task.py + ticket_service.py):
  - Status lifecycle: NEW -> IN_PROGRESS -> PENDING -> RESOLVED -> CLOSED
  - RESOLVED sets resolved_at = func.now()
  - CLOSED   sets closed_at   = func.now()
  - No state-machine validation; any status -> any status allowed.
"""
import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.task import Task, TaskPriority, TaskStatus, TaskType
from app.modules.tasks.services.ticket_service import TicketService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Set TEST_DATABASE_URL to an isolated MySQL database to run persistence tests.",
)

def _async_url(url):
    if url.startswith("mysql+pymysql://"):
        return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    return url

_NOW = datetime(2026, 7, 21, 12, 0, 0)

SEED_TASK = Task(
    id=2001, title="Robot cannot start", description="Startup fault code E1001",
    task_type=TaskType.PROBLEM, priority=TaskPriority.HIGH,
    status=TaskStatus.IN_PROGRESS,
    created_by="engineer-01", assigned_to="engineer-02", customer="customer-01",
    project_name="Test Project", project_id="project-A",
    tags=["startup", "fault"],
    created_at=_NOW - timedelta(days=3),
    updated_at=_NOW - timedelta(days=1),
    deadline_at=_NOW + timedelta(days=2),
    reply_count=2, view_count=5, source="manual",
)

@pytest.fixture(scope="module")
def mysql_schema():
    sync_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(sync_engine, tables=[Task.__table__])
    sync_engine.execute(Task.__table__.delete().where(Task.id == 2001))
    sync_engine.dispose()
    yield
    cleanup = create_engine(TEST_DATABASE_URL)
    cleanup.execute(Task.__table__.delete().where(Task.id == 2001))
    cleanup.dispose()

@pytest_asyncio.fixture
async def db_session(mysql_schema):
    engine = create_async_engine(_async_url(TEST_DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(SEED_TASK)
        await session.commit()
        yield session
        await session.rollback()
        await session.delete(SEED_TASK)
        await session.commit()
    await engine.dispose()

@pytest.mark.asyncio
async def test_update_status_persists_new_status(db_session):
    updated = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.RESOLVED)
    assert updated is not None
    assert updated.status == TaskStatus.RESOLVED
    persisted = (await db_session.execute(select(Task).where(Task.id == 2001))).scalar_one()
    assert persisted.status == TaskStatus.RESOLVED

@pytest.mark.asyncio
async def test_update_status_to_resolved_sets_resolved_at(db_session):
    updated = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.RESOLVED)
    assert updated.resolved_at is not None

@pytest.mark.asyncio
async def test_update_status_to_closed_sets_closed_at(db_session):
    updated = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.CLOSED)
    assert updated.closed_at is not None

@pytest.mark.asyncio
async def test_update_status_resolved_does_not_set_closed(db_session):
    updated = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.RESOLVED)
    assert updated.resolved_at is not None
    assert updated.closed_at is None

@pytest.mark.asyncio
async def test_update_status_full_lifecycle(db_session):
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.NEW)
    assert t.status == TaskStatus.NEW
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.IN_PROGRESS)
    assert t.status == TaskStatus.IN_PROGRESS
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.PENDING)
    assert t.status == TaskStatus.PENDING
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.RESOLVED)
    assert t.status == TaskStatus.RESOLVED
    assert t.resolved_at is not None
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.CLOSED)
    assert t.status == TaskStatus.CLOSED
    assert t.closed_at is not None

@pytest.mark.asyncio
async def test_update_status_nonexistent_returns_none(db_session):
    result = await TicketService.update_ticket_status(db_session, 99999, TaskStatus.RESOLVED)
    assert result is None

@pytest.mark.asyncio
async def test_update_status_any_transition_allowed(db_session):
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.NEW)
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.CLOSED)
    assert t.status == TaskStatus.CLOSED
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.IN_PROGRESS)
    assert t.status == TaskStatus.IN_PROGRESS

@pytest.mark.asyncio
async def test_update_status_updates_updated_at(db_session):
    original = await TicketService.get_ticket_by_id(db_session, 2001)
    t = await TicketService.update_ticket_status(db_session, 2001, TaskStatus.RESOLVED)
    assert t.updated_at > original.updated_at
