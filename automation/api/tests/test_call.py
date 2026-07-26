"""Phase 3 API tests: Call/QA/Stream/Message/MyTasks.

Conversation management, QA (sync + stream), messages, and user tasks.
Uses mock backend (no real infrastructure needed).
"""
import pytest

from automation.assertions import assert_status_code


@pytest.mark.api
class TestConversation:
    """Conversation CRUD (021-023)."""

    async def test_create_conversation(self, mock_api_client, mock_auth_header):
        """021: POST /api/conversations."""
        r = await mock_api_client.request("POST", "/api/conversations", headers=mock_auth_header,
                                           json={"title": "New conversation"})
        assert_status_code(r, 200)
        data = r.json()
        assert data["title"] == "New conversation"
        assert data["id"] > 0

    async def test_list_conversations(self, mock_api_client, mock_auth_header):
        """022: GET /api/conversations."""
        await mock_api_client.request("POST", "/api/conversations", headers=mock_auth_header,
                                       json={"title": "Conv A"})
        r = await mock_api_client.request("GET", "/api/conversations", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data
        assert data["total"] >= 1

    async def test_get_conversation_detail(self, mock_api_client, mock_auth_header):
        """023: GET /api/conversations/{id}."""
        create_r = await mock_api_client.request("POST", "/api/conversations", headers=mock_auth_header,
                                                  json={"title": "Detail test"})
        cid = create_r.json()["id"]
        r = await mock_api_client.request("GET", f"/api/conversations/{cid}", headers=mock_auth_header)
        assert_status_code(r, 200)
        assert r.json()["id"] == cid

    async def test_get_conversation_not_found(self, mock_api_client, mock_auth_header):
        """GET /api/conversations/{id} with non-existent id -> 404."""
        r = await mock_api_client.request("GET", "/api/conversations/99999", headers=mock_auth_header)
        assert r.status_code == 404


@pytest.mark.api
class TestQA:
    """QA endpoints (024 + stream)."""

    async def test_qa_ask(self, mock_api_client, mock_auth_header):
        """024: POST /api/qa/ask."""
        r = await mock_api_client.request("POST", "/api/qa/ask", headers=mock_auth_header,
                                           json={"question": "What is the status?"})
        assert_status_code(r, 200)
        data = r.json()
        assert data["success"] is True
        assert "What is the status?" in data["answer"]

    async def test_qa_ask_stream(self, mock_api_client, mock_auth_header):
        """POST /api/qa/ask/stream - SSE streaming endpoint."""
        r = await mock_api_client.request("POST", "/api/qa/ask/stream", headers=mock_auth_header,
                                           json={"question": "Tell me more"})
        assert_status_code(r, 200)
        data = r.json()
        assert data["event"] == "message"
        assert data["done"] is True
        assert "Tell me more" in data["data"]["content"]


@pytest.mark.api
class TestMessage:
    """Message operations (025)."""

    async def test_create_message(self, mock_api_client, mock_auth_header):
        """POST /api/messages."""
        r = await mock_api_client.request("POST", "/api/messages", headers=mock_auth_header,
                                           json={"content": "Test message"})
        assert_status_code(r, 200)
        assert r.json()["content"] == "Test message"

    async def test_list_messages(self, mock_api_client, mock_auth_header):
        """025: GET /api/messages."""
        r = await mock_api_client.request("GET", "/api/messages", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data


@pytest.mark.api
class TestMyTasks:
    """My tasks (026)."""

    async def test_get_my_tasks(self, mock_api_client, mock_auth_header):
        """026: GET /api/my-tasks/."""
        r = await mock_api_client.request("GET", "/api/my-tasks/", headers=mock_auth_header)
        assert_status_code(r, 200)
        data = r.json()
        assert "items" in data

    async def test_create_my_task(self, mock_api_client, mock_auth_header):
        """POST /api/my-tasks/."""
        r = await mock_api_client.request("POST", "/api/my-tasks/", headers=mock_auth_header,
                                           json={"title": "My task", "description": "desc"})
        assert_status_code(r, 200)
        assert r.json()["title"] == "My task"
