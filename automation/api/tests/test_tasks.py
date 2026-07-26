"""Phase 1 API tests: Task CRUD, status transitions, comments, AI assign.

Uses mock backend (no real infrastructure needed).
"""
import pytest

from automation.assertions import assert_status_code


@pytest.mark.api
class TestTaskCreate:
    """POST /api/tasks - create task (004-006)."""

    async def test_create_task_minimal(self, mock_api_client, mock_auth_header):
        """004: minimal required fields."""
        payload = {"title": "Test task", "description": "Test description"}
        r = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header, json=payload)
        assert_status_code(r, 200)
        data = r.json()
        assert data["title"] == "Test task"
        assert data["status"] == "pending"
        assert data["id"] > 0

    async def test_create_task_full_fields(self, mock_api_client, mock_auth_header):
        """005: all optional fields."""
        now = __import__("datetime").datetime.utcnow().isoformat()
        payload = {
            "title": "Full task", "description": "Full desc",
            "ticket_type": "problem", "priority": "high",
            "assigned_to": "engineer-01", "customer": "cust-01",
            "project_name": "Proj X", "project_id": "proj-01",
            "tags": ["urgent", "robot"], "metadata_info": {"source": "test"},
        }
        r = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header, json=payload)
        assert_status_code(r, 200)
        data = r.json()
        for k in payload:
            assert data[k] == payload[k], f"Mismatch at {k}"

    async def test_create_task_missing_title(self, mock_api_client, mock_auth_header):
        """006: missing required fields -> 422."""
        r = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header, json={})
        assert r.status_code == 422


@pytest.mark.api
class TestTaskList:
    """GET /api/tasks - list tasks (007-008)."""

    async def test_task_list(self, mock_api_client, mock_auth_header):
        """007: list returns tasks."""
        r = await mock_api_client.request("GET", "/api/tasks", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data
        assert "total" in data

    async def test_task_list_pagination(self, mock_api_client, mock_auth_header):
        """008: pagination params."""
        r = await mock_api_client.request("GET", "/api/tasks?page=1&size=5", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert data["page"] == 1
        assert data["size"] == 5


@pytest.mark.api
class TestTaskDetail:
    """GET /api/tasks/{id} - task detail (009-010)."""

    async def test_task_detail_found(self, mock_api_client, mock_auth_header):
        """009: existing task."""
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "Detail test", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("GET", f"/api/tasks/{tid}", headers=mock_auth_header)
        assert_status_code(r, 200)
        assert r.json()["id"] == tid

    async def test_task_detail_not_found(self, mock_api_client, mock_auth_header):
        """010: non-existent task -> 404."""
        r = await mock_api_client.request("GET", "/api/tasks/99999", headers=mock_auth_header)
        assert r.status_code == 404


@pytest.mark.api
class TestTaskUpdate:
    """PUT /api/tasks/{id} - update task (011)."""

    async def test_update_task(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "Before", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("PUT", f"/api/tasks/{tid}", headers=mock_auth_header,
                                           json={"title": "After", "priority": "high"})
        assert_status_code(r, 200)
        assert r.json()["title"] == "After"
        assert r.json()["priority"] == "high"


@pytest.mark.api
class TestTaskStatus:
    """PATCH /api/tasks/{id}/status - status transitions (012-013)."""

    async def test_valid_transition(self, mock_api_client, mock_auth_header):
        """012: pending -> in_progress."""
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "S", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("PATCH", f"/api/tasks/{tid}/status", headers=mock_auth_header,
                                           json={"status": "in_progress"})
        assert_status_code(r, 200)
        assert r.json()["status"] == "in_progress"

    async def test_invalid_transition(self, mock_api_client, mock_auth_header):
        """013: pending -> closed (invalid) -> 400."""
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "S", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("PATCH", f"/api/tasks/{tid}/status", headers=mock_auth_header,
                                           json={"status": "closed"})
        assert r.status_code == 400


@pytest.mark.api
class TestTaskAssign:
    """PATCH /api/tasks/{id}/assign - assign task (014)."""

    async def test_assign_task(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "Assign me", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("PATCH", f"/api/tasks/{tid}/assign", headers=mock_auth_header,
                                           json={"assigned_to": "engineer-02"})
        assert_status_code(r, 200)
        assert r.json()["assigned_to"] == "engineer-02"


@pytest.mark.api
class TestTaskDelete:
    """DELETE /api/tasks/{id} - delete task (015)."""

    async def test_delete_task(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "Delete me", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("DELETE", f"/api/tasks/{tid}", headers=mock_auth_header)
        assert r.status_code == 204
        r2 = await mock_api_client.request("GET", f"/api/tasks/{tid}", headers=mock_auth_header)
        assert r2.status_code == 404


@pytest.mark.api
class TestTaskFilter:
    """POST /api/tasks/filter - filter tasks (016)."""

    async def test_filter_tasks(self, mock_api_client, mock_auth_header):
        await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                       json={"title": "Robot error", "description": "E1001", "priority": "high"})
        await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                       json={"title": "Network issue", "description": "timeout", "priority": "low"})
        r = await mock_api_client.request("POST", "/api/tasks/filter", headers=mock_auth_header,
                                           json={"keyword": "robot"})
        assert_status_code(r, 200)
        assert r.json()["total"] >= 1


@pytest.mark.api
class TestTaskStats:
    """GET /api/tasks/stats/overview - task statistics (017)."""

    async def test_task_stats(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("GET", "/api/tasks/stats/overview", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "total" in data
        assert "by_status" in data


@pytest.mark.api
class TestTaskComments:
    """POST+GET /api/tasks/{id}/comments - comment CRUD (018-019)."""

    async def test_create_comment(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "T", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("POST", f"/api/tasks/{tid}/comments", headers=mock_auth_header,
                                           json={"content": "This is a comment"})
        assert r.status_code == 201
        assert r.json()["content"] == "This is a comment"

    async def test_list_comments(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "T2", "description": "x"})
        tid = create.json()["id"]
        await mock_api_client.request("POST", f"/api/tasks/{tid}/comments", headers=mock_auth_header,
                                       json={"content": "C1"})
        r = await mock_api_client.request("GET", f"/api/tasks/{tid}/comments", headers=mock_auth_header)
        assert_status_code(r, 200)
        assert len(r.json()) >= 1

    async def test_comment_on_nonexistent_task(self, mock_api_client, mock_auth_header):
        r = await mock_api_client.request("POST", "/api/tasks/99999/comments", headers=mock_auth_header,
                                           json={"content": "x"})
        assert r.status_code == 404


@pytest.mark.api
class TestTaskAiAssign:
    """POST /api/tasks/{id}/ai-assign - AI auto assign (020)."""

    async def test_ai_assign(self, mock_api_client, mock_auth_header):
        create = await mock_api_client.request("POST", "/api/tasks", headers=mock_auth_header,
                                                json={"title": "AI me", "description": "x"})
        tid = create.json()["id"]
        r = await mock_api_client.request("POST", f"/api/tasks/{tid}/ai-assign", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "assigned_to" in data
        assert "confidence" in data
