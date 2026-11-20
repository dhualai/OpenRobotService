"""API-level coverage for POST /api/tasks/ standard task creation.

This suite intentionally isolates the router from MySQL, notifications and AI.
Persistence is covered by ``test_standard_task_creation_db.py``.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.tasks.api.task import comment_attachment_map, create_task, get_db, router
from app.modules.tasks.services.ticket_service import TicketService


def _created_task(**overrides):
    """Return the minimum persisted task shape expected by the response schema."""
    data = {
        "id": 1001,
        "title": "机器人无法启动",
        "description": "启动后显示故障码 E1001",
        "ticket_type": "problem",
        "priority": "high",
        "status": "pending",
        "created_by": "system",
        "assigned_to": "engineer-01",
        "customer": "customer-01",
        "team": None,
        "project_name": "测试项目",
        "project_id": "project-01",
        "related_resource_id": None,
        "tags": ["启动", "故障"],
        "metadata_info": {"source": "test"},
        "attachments": ["helpdesk-comment/temp-001/log.txt"],
        "created_at": datetime(2026, 7, 20, 10, 0, 0),
        "updated_at": datetime(2026, 7, 20, 10, 0, 0),
        "resolved_at": None,
        "closed_at": None,
        "deadline_at": None,
        "reply_count": 1,
        "view_count": 0,
        "comments": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.fixture
def create_task_mock(monkeypatch):
    mock = AsyncMock(return_value=_created_task())
    monkeypatch.setattr(TicketService, "create_ticket", mock)
    return mock


@pytest.fixture
def client(monkeypatch, create_task_mock):
    """Create the smallest application that exposes only the standard task router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db
    comment_attachment_map.clear()

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def valid_payload():
    return {
        "title": "机器人无法启动",
        "description": "启动后显示故障码 E1001",
        "ticket_type": "problem",
        "priority": "high",
        "customer": "customer-01",
        "project_name": "测试项目",
        "project_id": "project-01",
        "tags": ["启动", "故障"],
        "metadata_info": {"source": "test"},
        "attachments": ["temp-001"],
        "assigned_to": "engineer-01",
    }


def test_create_standard_task_returns_persisted_task_contract(client, valid_payload):
    """TC-T01: a valid submission returns the standard task contract."""
    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1001
    assert body["title"] == valid_payload["title"]
    assert body["description"] == valid_payload["description"]
    assert body["ticket_type"] == valid_payload["ticket_type"]
    assert body["priority"] == valid_payload["priority"]
    assert body["status"] == "pending"
    assert body["created_by"] == "system"
    assert body["assigned_to"] == valid_payload["assigned_to"]
    assert body["customer"] == valid_payload["customer"]
    assert body["project_name"] == valid_payload["project_name"]
    assert body["project_id"] == valid_payload["project_id"]
    assert body["tags"] == valid_payload["tags"]
    assert body["metadata_info"] == valid_payload["metadata_info"]
    assert body["reply_count"] == 1
    assert body["attachments"] == ["helpdesk-comment/temp-001/log.txt"]
    assert body["created_at"] == "2026-07-20T10:00:00"
    assert body["updated_at"] == "2026-07-20T10:00:00"


def test_create_standard_task_passes_request_and_system_identity_to_service(
    client, valid_payload, create_task_mock
):
    """The router must forward the complete request contract to the service."""
    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 200
    create_task_mock.assert_awaited_once()
    db, ticket_data, created_by, attachment_map, token = create_task_mock.await_args.args
    assert ticket_data.title == valid_payload["title"]
    assert ticket_data.description == valid_payload["description"]
    assert ticket_data.ticket_type.value == valid_payload["ticket_type"]
    assert ticket_data.priority.value == valid_payload["priority"]
    assert ticket_data.tags == valid_payload["tags"]
    assert ticket_data.metadata_info == valid_payload["metadata_info"]
    assert created_by == "system"
    assert attachment_map is comment_attachment_map
    assert token is None
    assert db is not None


@pytest.mark.parametrize("field", ["title", "description"])
def test_create_standard_task_rejects_missing_required_fields(
    client, valid_payload, create_task_mock, field
):
    """TC-T02: required business fields are rejected before task creation."""
    valid_payload.pop(field)

    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 422
    assert any(error["loc"][-1] == field for error in response.json()["detail"])
    create_task_mock.assert_not_awaited()


def test_create_standard_task_rejects_invalid_task_type(
    client, valid_payload, create_task_mock
):
    """TC-T02: invalid task type must not be accepted as a standard task."""
    valid_payload["ticket_type"] = "invalid-type"

    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 422
    create_task_mock.assert_not_awaited()


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.xfail(
    strict=True,
    reason="The current request schema accepts empty and whitespace-only business text.",
)
def test_create_standard_task_rejects_empty_or_whitespace_business_text(
    client, valid_payload, create_task_mock, field
):
    """Business rule: a task must contain meaningful title and description text."""
    valid_payload[field] = "   "

    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 422
    create_task_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [("tags", "not-a-list"), ("metadata_info", ["not-a-dict"]), ("attachments", "not-a-list")],
)
def test_create_standard_task_rejects_invalid_collection_shapes(
    client, valid_payload, create_task_mock, field, value
):
    valid_payload[field] = value

    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 422
    create_task_mock.assert_not_awaited()


def test_create_standard_task_returns_error_when_business_creation_fails(client, valid_payload, monkeypatch):
    """TC-T08: a service failure is surfaced and never reported as successful creation."""
    monkeypatch.setattr(
        TicketService,
        "create_ticket",
        AsyncMock(side_effect=RuntimeError("database transaction failed")),
    )

    response = client.post("/api/tasks/", json=valid_payload)

    assert response.status_code == 500
    assert "创建任务失败" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_standard_task_handler_uses_authenticated_user_identity(monkeypatch, valid_payload):
    """The handler forwards the authenticated identity when a user context is supplied."""
    create_task_mock = AsyncMock(return_value=_created_task(created_by="creator-01"))
    monkeypatch.setattr(TicketService, "create_ticket", create_task_mock)
    payload = __import__("app.modules.tasks.schemas.ticket", fromlist=["TicketCreate"]).TicketCreate(
        **valid_payload
    )
    current_user = SimpleNamespace(username="creator-01", token="jwt-for-test")

    result = await create_task(payload, db=object(), current_user=current_user)

    assert result.created_by == "creator-01"
    assert create_task_mock.await_args.args[2] == "creator-01"
    assert create_task_mock.await_args.args[4] == "jwt-for-test"


@pytest.mark.xfail(
    strict=True,
    reason="POST /api/tasks/ currently declares Depends(lambda: None), not the JWT user dependency.",
)
def test_create_standard_task_api_uses_bearer_identity(client, valid_payload, create_task_mock):
    """Acceptance rule: API authentication must determine the persisted creator."""
    response = client.post(
        "/api/tasks/",
        json=valid_payload,
        headers={"Authorization": "Bearer jwt-for-creator-01"},
    )

    assert response.status_code == 200
    assert create_task_mock.await_args.args[2] == "creator-01"
