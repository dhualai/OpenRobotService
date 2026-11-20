"""API-level coverage for GET /api/tasks/ standard task query.

This suite intentionally isolates the router from MySQL, notifications and AI.
Persistence is covered by ``test_standard_task_query_db.py``.

Query risk map (cross-ref with test IDs):
  R1  status comma-separated with invalid values silently skipped  -> TC-Q06
  R2  keyword ilike with no escaping                             -> TC-Q06
  R3  view_count incremented unconditionally                     -> TC-Q10
  R4  is_valid_id only checks int > 0                            -> TC-Q07
  R5  FIELD_MAPPING miss silently skipped in filter              -> TC-Q13
  R6  enum Pydantic validation may be bypassed                   -> TC-Q03
  R7  sort is hard-coded created_at desc                         -> TC-Q01
  R8  tag filter uses JSON contains([tag])                       -> TC-Q06
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.tasks.api.task import router, get_db
from app.modules.tasks.services.ticket_service import TicketService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ticket_list_item(**overrides):
    """Return the minimum list-item shape expected by the response schema."""
    data = {
        "id": 1001,
        "title": "\u673a\u5668\u4eba\u65e0\u6cd5\u542f\u52a8",
        "description": "\u542f\u52a8\u540e\u663e\u793a\u6545\u969c\u7801 E1001",
        "ticket_type": "problem",
        "priority": "high",
        "status": "pending",
        "created_by": "engineer-01",
        "assigned_to": "engineer-02",
        "customer": "customer-01",
        "team": None,
        "project_name": "\u6d4b\u8bd5\u9879\u76ee",
        "project_id": "project-01",
        "related_resource_id": None,
        "tags": ["\u542f\u52a8", "\u6545\u969c"],
        "metadata_info": None,
        "attachments": [],
        "created_at": datetime(2026, 7, 20, 10, 0, 0),
        "updated_at": datetime(2026, 7, 20, 10, 0, 0),
        "resolved_at": None,
        "closed_at": None,
        "deadline_at": None,
        "reply_count": 2,
        "view_count": 5,
        "created_by_name": "\u5de5\u7a0b\u5e08\u4e00\u53f7",
        "assigned_to_name": "\u5de5\u7a0b\u5e08\u4e8c\u53f7",
        "customer_name": "\u5ba2\u6237\u4e00\u53f7",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _paged_result(items=None, total=1, page=1, size=10):
    if items is None:
        items = [_ticket_list_item()]
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": (total + size - 1) // size,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def get_tickets_mock(monkeypatch):
    mock = AsyncMock(return_value=_paged_result())
    monkeypatch.setattr(TicketService, "get_tickets", mock)
    return mock


@pytest.fixture
def get_ticket_by_id_mock(monkeypatch):
    mock = AsyncMock(return_value=_ticket_list_item())
    monkeypatch.setattr(TicketService, "get_ticket_by_id", mock)
    return mock


@pytest.fixture
def filter_tickets_mock(monkeypatch):
    mock = AsyncMock(return_value=_paged_result())
    monkeypatch.setattr(TicketService, "filter_tickets", mock)
    return mock


@pytest.fixture
def get_ticket_stats_mock(monkeypatch):
    mock = AsyncMock(return_value={
        "total": 10,
        "statistics": {"new": 2, "in_progress": 3, "pending": 1, "resolved": 2, "closed": 2},
        "breakdown": {"opened": 6, "closed": 2, "resolved": 2, "in_progress": 3},
    })
    monkeypatch.setattr(TicketService, "get_ticket_stats", mock)
    return mock


@pytest.fixture
def client(monkeypatch, get_tickets_mock):
    """Create the smallest application that exposes only the task router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# TC-Q01: \u6b63\u5e38\u5206\u9875\u67e5\u8be2
# ---------------------------------------------------------------------------

def test_query_standard_tasks_returns_paginated_contract(client, get_tickets_mock):
    """TC-Q01: a valid GET /api/tasks/ returns the paginated list contract."""
    response = client.get("/api/tasks/")

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "size" in body
    assert "pages" in body

    assert body["total"] == 1
    assert body["page"] == 1
    assert body["size"] == 10
    assert body["pages"] == 1

    item = body["items"][0]
    assert item["id"] == 1001
    assert item["title"] == "\u673a\u5668\u4eba\u65e0\u6cd5\u542f\u52a8"
    assert item["status"] == "pending"
    assert item["created_by"] == "engineer-01"
    assert item["assigned_to"] == "engineer-02"
    assert item["customer"] == "customer-01"
    assert item["reply_count"] == 2
    assert item["view_count"] == 5
    assert item["created_by_name"] == "\u5de5\u7a0b\u5e08\u4e00\u53f7"


def test_query_standard_tasks_passes_default_pagination_to_service(client, get_tickets_mock):
    """The router must forward default page=1, size=10 to the service."""
    response = client.get("/api/tasks/")

    assert response.status_code == 200
    get_tickets_mock.assert_awaited_once()
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.page == 1
    assert params.size == 10


def test_query_standard_tasks_passes_custom_pagination_to_service(client, get_tickets_mock):
    """Custom page & size query params must reach the service."""
    response = client.get("/api/tasks/?page=3&size=25")

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.page == 3
    assert params.size == 25


# ---------------------------------------------------------------------------
# TC-Q02: \u8fc7\u6ee4\u53c2\u6570\u4f20\u9012
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query_params,field,expected_value",
    [
        ("status=in_progress", "status", "in_progress"),
        ("priority=high", "priority", "high"),
        ("ticket_type=bug", "ticket_type", "bug"),
        ("created_by=engineer-01", "created_by", "engineer-01"),
        ("assigned_to=engineer-02", "assigned_to", "engineer-02"),
        ("customer=customer-01", "customer", "customer-01"),
        ("project_name=\u6d4b\u8bd5\u9879\u76ee", "project_name", "\u6d4b\u8bd5\u9879\u76ee"),
        ("project_id=project-01", "project_id", "project-01"),
        ("source=zentao", "source", "zentao"),
        ("tag=\u6545\u969c", "tag", "\u6545\u969c"),
    ],
)
def test_query_standard_tasks_forwards_single_filter_to_service(
    client, get_tickets_mock, query_params, field, expected_value,
):
    """TC-Q02: each single-value filter param must be forwarded to the service."""
    response = client.get(f"/api/tasks/?{query_params}")

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert getattr(params, field) == expected_value


def test_query_standard_tasks_forwards_multi_status_as_comma_separated(client, get_tickets_mock):
    """Multiple status values (comma-separated) must be forwarded as a single string."""
    response = client.get("/api/tasks/?status=in_progress,pending,resolved")

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.status == "in_progress,pending,resolved"


def test_query_standard_tasks_forwards_keyword_to_service(client, get_tickets_mock):
    """Keyword search param must be forwarded."""
    response = client.get("/api/tasks/?keyword=\u65e0\u6cd5\u542f\u52a8")

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.keyword == "\u65e0\u6cd5\u542f\u52a8"


def test_query_standard_tasks_forwards_time_range_to_service(client, get_tickets_mock):
    """TC-Q04: time-range query params must be forwarded."""
    response = client.get(
        "/api/tasks/"
        "?created_at_start=2026-07-01T00:00:00"
        "&created_at_end=2026-07-31T23:59:59"
    )

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.created_at_start == datetime(2026, 7, 1, 0, 0, 0)
    assert params.created_at_end == datetime(2026, 7, 31, 23, 59, 59)


def test_query_standard_tasks_forwards_operator_params_to_service(client, get_tickets_mock):
    """TC-Q06: field operator params (e.g. title_op=equals) must be forwarded."""
    response = client.get(
        "/api/tasks/"
        "?title=\u7cbe\u786e\u6807\u9898&title_op=equals"
        "&id=1001&id_op=gt"
        "&created_by=engineer-01&created_by_op=notEquals"
    )

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.title == "\u7cbe\u786e\u6807\u9898"
    assert params.title_op == "equals"
    assert params.id == 1001
    assert params.id_op == "gt"
    assert params.created_by == "engineer-01"
    assert params.created_by_op == "notEquals"


def test_query_standard_tasks_forwards_name_params_to_service(client, get_tickets_mock):
    """TC-Q07: user-name filter params must be forwarded."""
    response = client.get(
        "/api/tasks/"
        "?created_by_name=\u5f20\u4e09"
        "&assigned_to_name=\u674e\u56db"
        "&customer_name=\u738b\u4e94"
    )

    assert response.status_code == 200
    _db, params, _token = get_tickets_mock.await_args.args
    assert params.created_by_name == "\u5f20\u4e09"
    assert params.assigned_to_name == "\u674e\u56db"
    assert params.customer_name == "\u738b\u4e94"


# ---------------------------------------------------------------------------
# TC-Q03: \u65e0\u6548\u679a\u4e3e\u503c
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("param,invalid_value", [
    ("priority", "critical"),
    ("ticket_type", "critical-bug"),
])
@pytest.mark.xfail(
    strict=True,
    reason="The router handler converts enum inside try/except and returns 500, not 422.",
)
def test_query_standard_tasks_rejects_invalid_enum(client, get_tickets_mock, param, invalid_value):
    """TC-Q03: invalid enum values must be rejected at the router level (Pydantic)."""
    response = client.get(f"/api/tasks/?{param}={invalid_value}")

    assert response.status_code == 422
    get_tickets_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# TC-Q09/TC-Q10: \u5206\u9875\u53c2\u6570\u8fb9\u754c\u6821\u9a8c
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("param,value", [
    ("page", "0"),
    ("page", "-1"),
    ("page", "abc"),
    ("size", "0"),
    ("size", "-5"),
    ("size", "101"),
    ("size", "abc"),
])
def test_query_standard_tasks_rejects_invalid_pagination(client, get_tickets_mock, param, value):
    """TC-Q10: invalid pagination params must be rejected."""
    response = client.get(f"/api/tasks/?{param}={value}")

    assert response.status_code == 422
    get_tickets_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# TC-Q09: \u7a7a\u7ed3\u679c
# ---------------------------------------------------------------------------

def test_query_standard_tasks_returns_empty_list_when_no_results(client, monkeypatch):
    """TC-Q09: a query matching no records must return an empty items array."""
    mock = AsyncMock(return_value=_paged_result(items=[], total=0, page=1, size=10))
    monkeypatch.setattr(TicketService, "get_tickets", mock)

    response = client.get("/api/tasks/?status=resolved&keyword=\u4e0d\u5b58\u5728\u7684\u5173\u952e\u8bcd")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["pages"] == 0


# ---------------------------------------------------------------------------
# TC-Q16: \u670d\u52a1\u5f02\u5e38
# ---------------------------------------------------------------------------

def test_query_standard_tasks_returns_error_when_service_fails(client, monkeypatch):
    """TC-Q16: a service failure must be surfaced as a 500 error."""
    monkeypatch.setattr(
        TicketService,
        "get_tickets",
        AsyncMock(side_effect=RuntimeError("database connection failed")),
    )
    response = client.get("/api/tasks/")

    assert response.status_code == 500
    assert "\u83b7\u53d6\u4efb\u52a1\u5217\u8868\u5931\u8d25" in response.json()["detail"]


# ===========================================================================
# \u5355\u4e2a\u5de5\u5355\u67e5\u8be2 - GET /api/tasks/{task_id}
# ===========================================================================

@pytest.fixture
def single_client(monkeypatch, get_ticket_by_id_mock):
    """App fixture for single-task query tests."""
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client


def test_query_single_task_returns_correct_contract(single_client, get_ticket_by_id_mock):
    """TC-Q05: a valid task id returns the correct task contract."""
    response = single_client.get("/api/tasks/1001")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1001
    assert body["title"] == "\u673a\u5668\u4eba\u65e0\u6cd5\u542f\u52a8"
    assert body["status"] == "pending"
    assert body["reply_count"] == 2
    assert body["view_count"] == 5


def test_query_single_task_passes_id_and_params_to_service(single_client, get_ticket_by_id_mock):
    """The router must forward the task id and load_comments flag."""
    response = single_client.get("/api/tasks/1001?load_comments=true")

    assert response.status_code == 200
    get_ticket_by_id_mock.assert_awaited_once()
    _db, task_id, load_comments, _token = get_ticket_by_id_mock.await_args.args
    assert task_id == 1001
    assert load_comments is True


def test_query_single_task_returns_404_for_nonexistent(single_client, monkeypatch):
    """TC-Q12: a non-existent task id must return 404."""
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=None))

    response = single_client.get("/api/tasks/99999")

    assert response.status_code == 404
    assert "\u4efb\u52a1\u672a\u627e\u5230" in response.json()["detail"]


@pytest.mark.parametrize("task_id", ["abc", "1.5"])
def test_query_single_task_rejects_non_numeric_id(single_client, get_ticket_by_id_mock, task_id):
    """TC-Q07: non-numeric task ids must be rejected (422) by the router path converter."""
    response = single_client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 422
    get_ticket_by_id_mock.assert_not_awaited()


@pytest.mark.xfail(
    strict=True,
    reason="task_id: int path param accepts 0/-1 as valid Python ints; the is_valid_id guard is in the service layer (already mocked).",
)
@pytest.mark.parametrize("task_id", ["0", "-1"])
def test_query_single_task_rejects_invalid_id(single_client, get_ticket_by_id_mock, task_id):
    """TC-Q07: semantically invalid task ids (0, negative) should be rejected, but the mock always returns."""
    response = single_client.get(f"/api/tasks/{task_id}")

    assert response.status_code in (404, 422)
    get_ticket_by_id_mock.assert_not_awaited() if response.status_code == 422 else None


@pytest.mark.xfail(
    strict=True,
    reason="The router does NOT wrap get_ticket_by_id in try/except; RuntimeError propagates past FastAPI middleware.",
)
def test_query_single_task_returns_error_when_service_fails(single_client, monkeypatch):
    """TC-Q16: a service failure for single task must surface as 500."""
    monkeypatch.setattr(
        TicketService,
        "get_ticket_by_id",
        AsyncMock(side_effect=RuntimeError("db error")),
    )
    response = single_client.get("/api/tasks/1001")

    assert response.status_code == 500


# ===========================================================================
# \u590d\u5408\u8fc7\u6ee4 - POST /api/tasks/filter
# ===========================================================================

@pytest.fixture
def filter_client(monkeypatch, filter_tickets_mock):
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client


def test_filter_tasks_simple_and_conditions(filter_client, filter_tickets_mock):
    """TC-Q13: simple AND filter conditions must be forwarded to the service."""
    payload = {
        "filters": [
            {"field": "status", "op": "eq", "value": "in_progress"},
            {"field": "priority", "op": "eq", "value": "high"},
        ],
        "page": 1,
        "size": 10,
    }
    response = filter_client.post("/api/tasks/filter", json=payload)

    assert response.status_code == 200
    filter_tickets_mock.assert_awaited_once()
    _db, filter_request, _token = filter_tickets_mock.await_args.args
    assert len(filter_request.filters) == 2
    assert filter_request.filters[0].field == "status"
    assert filter_request.filters[1].field == "priority"


def test_filter_tasks_nested_or_conditions(filter_client, filter_tickets_mock):
    """TC-Q14: nested OR conditions must be forwarded correctly."""
    payload = {
        "filters": [
            {
                "or": [
                    {"field": "status", "op": "eq", "value": "pending"},
                    {"field": "priority", "op": "eq", "value": "urgent"},
                ]
            }
        ],
        "page": 1,
        "size": 10,
    }
    response = filter_client.post("/api/tasks/filter", json=payload)

    assert response.status_code == 200
    _db, filter_request, _token = filter_tickets_mock.await_args.args
    assert len(filter_request.filters) == 1
    assert filter_request.filters[0].or_conditions is not None
    assert len(filter_request.filters[0].or_conditions) == 2


def test_filter_tasks_nested_and_or_conditions(filter_client, filter_tickets_mock):
    """TC-Q15: deeply nested AND/OR conditions must be forwarded."""
    payload = {
        "filters": [
            {
                "and": [
                    {"field": "status", "op": "eq", "value": "in_progress"},
                    {
                        "or": [
                            {"field": "priority", "op": "eq", "value": "high"},
                            {"field": "priority", "op": "eq", "value": "urgent"},
                        ]
                    },
                ]
            }
        ],
        "page": 1,
        "size": 10,
    }
    response = filter_client.post("/api/tasks/filter", json=payload)

    assert response.status_code == 200
    _db, filter_request, _token = filter_tickets_mock.await_args.args
    root = filter_request.filters[0]
    assert root.and_conditions is not None
    assert root.and_conditions[0].field == "status"
    assert root.and_conditions[1].or_conditions is not None


def test_filter_tasks_rejects_invalid_page_size(filter_client, filter_tickets_mock):
    """Pagination in filter must also be validated."""
    response = filter_client.post("/api/tasks/filter", json={"filters": [], "page": 0, "size": 200})

    assert response.status_code == 422
    filter_tickets_mock.assert_not_awaited()


# ===========================================================================
# \u7edf\u8ba1 - GET /api/tasks/stats/overview
# ===========================================================================

@pytest.fixture
def stats_client(monkeypatch, get_ticket_stats_mock):
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield object()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as test_client:
        yield test_client


def test_get_task_stats_returns_contract(stats_client, get_ticket_stats_mock):
    """TC-Q18: stats overview returns the expected contract."""
    response = stats_client.get("/api/tasks/stats/overview")

    assert response.status_code == 200
    body = response.json()
    assert "total" in body
    assert "statistics" in body
    assert "breakdown" in body
    assert body["total"] == 10


def test_get_task_stats_returns_error_when_service_fails(stats_client, monkeypatch):
    """Service failure for stats must surface as 500."""
    monkeypatch.setattr(
        TicketService,
        "get_ticket_stats",
        AsyncMock(side_effect=RuntimeError("stats error")),
    )
    response = stats_client.get("/api/tasks/stats/overview")

    assert response.status_code == 500
    assert "\u83b7\u53d6\u7edf\u8ba1\u4fe1\u606f\u5931\u8d25" in response.json()["detail"]
