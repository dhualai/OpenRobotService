"""API-level coverage for PATCH /api/tasks/{task_id}/status standard task status transition.

This suite intentionally isolates the router from MySQL, notifications and AI.
Persistence and resolved_at/closed_at timestamp side-effects are covered by
``test_standard_task_status_transition_db.py``.

Business rules from PRD:
  - Status lifecycle: NEW -> IN_PROGRESS -> PENDING -> RESOLVED -> CLOSED
  - resolved_at set when transitioning to RESOLVED; closed_at when to CLOSED.
  - Only created_by, assigned_to, or admin may change status (403 otherwise).
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_utils import LoggingTestClient

from app.modules.tasks.api.task import router, get_db
from app.modules.tasks.services.ticket_service import TicketService
from app.modules.tasks.models.ticket import TaskStatus


def _ticket_with_status(status_value: str, **overrides):
    data = {
        "id": 1001, "title": "机器人无法启动",
        "description": "启动后显示故障码 E1001",
        "ticket_type": "problem", "priority": "high",
        "status": status_value, "created_by": "",
        "assigned_to": "engineer-02", "customer": "customer-01",
        "team": None, "project_name": "测试项目", "project_id": "project-01",
        "related_resource_id": None, "tags": [], "metadata_info": None,
        "attachments": [],
        "created_at": datetime(2026, 7, 20, 10, 0, 0),
        "updated_at": datetime(2026, 7, 21, 10, 0, 0),
        "resolved_at": None, "closed_at": None, "deadline_at": None,
        "reply_count": 0, "view_count": 5, "comments": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.fixture
def get_ticket_mock(monkeypatch):
    mock = AsyncMock(return_value=_ticket_with_status("pending", created_by=""))
    monkeypatch.setattr(TicketService, "get_ticket_by_id", mock)
    return mock


@pytest.fixture
def update_status_mock(monkeypatch):
    async def _fake(db, tid, status_enum):
        return _ticket_with_status(status_enum.value, created_by="")
    mock = AsyncMock(side_effect=_fake)
    monkeypatch.setattr(TicketService, "update_ticket_status", mock)
    return mock


@pytest.fixture
def client(monkeypatch, get_ticket_mock, update_status_mock):
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")
    async def override_db():
        yield object()
    app.dependency_overrides[get_db] = override_db
    with LoggingTestClient(app) as test_client:
        yield test_client


def test_update_task_status_returns_updated_contract(client, update_status_mock):
    """TC-S01: a valid PATCH returns the updated task with new status."""
    response = client.patch("/api/tasks/1001/status?status=in_progress")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1001
    assert body["status"] == "in_progress"
    assert body["title"] == "机器人无法启动"
    assert body["ticket_type"] == "problem"
    assert body["priority"] == "high"
    assert body["assigned_to"] == "engineer-02"
    assert body["customer"] == "customer-01"


def test_update_task_status_forwards_new_status_to_service(client, update_status_mock):
    """The router must forward the target status to the service."""
    response = client.patch("/api/tasks/1001/status?status=resolved")
    assert response.status_code == 200
    update_status_mock.assert_awaited_once()
    _db, task_id, status_enum = update_status_mock.await_args.args
    assert task_id == 1001
    assert status_enum == TaskStatus.RESOLVED


def test_update_task_status_passes_task_id_to_get_ticket(client, get_ticket_mock):
    """The router must query the original ticket using the path task_id."""
    response = client.patch("/api/tasks/1001/status?status=resolved")
    assert response.status_code == 200
    get_ticket_mock.assert_awaited_once()
    _db, task_id = get_ticket_mock.await_args.args
    assert task_id == 1001


def test_update_task_status_returns_404_for_nonexistent(client, monkeypatch):
    """TC-S04: a non-existent task id must return 404."""
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=None))
    response = client.patch("/api/tasks/99999/status?status=in_progress")
    assert response.status_code == 404
    assert "任务未找到" in response.json()["detail"]


def test_update_task_status_rejects_invalid_status(client, get_ticket_mock):
    """TC-S05: an invalid status string must return 400."""
    response = client.patch("/api/tasks/1001/status?status=unknown_status")
    assert response.status_code == 400


def test_update_task_status_rejects_unauthorized_user(client, monkeypatch):
    """TC-S06: non-creator/assignee/admin gets 403."""
    ticket = _ticket_with_status("pending", created_by="engineer-01", assigned_to="engineer-02")
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=ticket))
    response = client.patch("/api/tasks/1001/status?status=in_progress")
    assert response.status_code == 403
    assert "无权限更新任务状态" in response.json()["detail"]


def test_update_task_status_returns_error_when_service_fails(client, get_ticket_mock, monkeypatch):
    """TC-S07: a service failure must be surfaced as 500."""
    monkeypatch.setattr(
        TicketService, "update_ticket_status",
        AsyncMock(side_effect=RuntimeError("transaction failed")),
    )
    response = client.patch("/api/tasks/1001/status?status=resolved")
    assert response.status_code == 500
    assert "更新任务状态失败" in response.json()["detail"]


def test_update_task_status_handles_all_state_values(client, get_ticket_mock, update_status_mock):
    """TC-S08 (R1): the router accepts every valid TaskStatus value.
    Current code does NOT enforce state-machine ordering; any status -> any status is allowed.
    """
    for sv in ("new", "in_progress", "pending", "resolved", "closed"):
        response = client.patch(f"/api/tasks/1001/status?status={sv}")
        assert response.status_code == 200
        assert response.json()["status"] == sv


@pytest.mark.asyncio
async def test_update_task_status_allows_creator(monkeypatch):
    """The creator can change status (handler invoked directly with identity)."""
    from app.modules.tasks.api.task import update_task_status as handler
    ticket = _ticket_with_status("pending", created_by="creator-01", assigned_to="engineer-02")
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=ticket))
    update_mock = AsyncMock(return_value=_ticket_with_status("in_progress", created_by="creator-01"))
    monkeypatch.setattr(TicketService, "update_ticket_status", update_mock)
    cu = SimpleNamespace(username="creator-01", is_admin=False, token=None)
    result = await handler(task_id=1001, status="in_progress", db=object(), current_user=cu)
    assert result.status == "in_progress"


@pytest.mark.asyncio
async def test_update_task_status_allows_assignee(monkeypatch):
    """The assignee can change status."""
    from app.modules.tasks.api.task import update_task_status as handler
    ticket = _ticket_with_status("pending", created_by="engineer-01", assigned_to="assignee-01")
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=ticket))
    update_mock = AsyncMock(return_value=_ticket_with_status("in_progress", created_by="engineer-01", assigned_to="assignee-01"))
    monkeypatch.setattr(TicketService, "update_ticket_status", update_mock)
    cu = SimpleNamespace(username="assignee-01", is_admin=False, token=None)
    result = await handler(task_id=1001, status="in_progress", db=object(), current_user=cu)
    assert result.status == "in_progress"


@pytest.mark.asyncio
async def test_update_task_status_allows_admin(monkeypatch):
    """An admin can change any task's status."""
    from app.modules.tasks.api.task import update_task_status as handler
    ticket = _ticket_with_status("pending", created_by="engineer-01", assigned_to="engineer-02")
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=ticket))
    update_mock = AsyncMock(return_value=_ticket_with_status("resolved"))
    monkeypatch.setattr(TicketService, "update_ticket_status", update_mock)
    cu = SimpleNamespace(username="admin-user", is_admin=True, token=None)
    result = await handler(task_id=1001, status="resolved", db=object(), current_user=cu)
    assert result.status == "resolved"
