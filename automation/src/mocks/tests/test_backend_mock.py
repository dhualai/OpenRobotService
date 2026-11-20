"""Unit tests for MockBackend ticket-driven routes.

Covers the routes added for ticket-driven generated cases:
spec-doc / operation-logs / relations / wechat read-only, plus the
trailing-slash normalization in ``handle()``.
"""

import httpx
import pytest

from automation.src.mocks.backend_mock import MockBackend


@pytest.fixture
def client():
    backend = MockBackend()
    transport = httpx.MockTransport(backend.handle)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/auth/login", json={"username": "testadmin", "password": "admin123"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_trailing_slash_create_and_list(client):
    token = await _login(client)

    resp = await client.post("/api/tasks/", json={
        "title": "尾斜杠创建", "description": "desc", "ticket_type": "bug"},
        headers=_auth(token))
    assert resp.status_code == 200

    resp = await client.get("/api/tasks/")
    assert resp.status_code == 200
    data = resp.json()
    assert any(item.get("title") == "尾斜杠创建" for item in data.get("items", []))


async def test_spec_doc_roundtrip(client):
    resp = await client.get("/api/tasks/1/spec-doc")
    assert resp.status_code == 200
    assert resp.json()["content"] == ""

    resp = await client.put("/api/tasks/1/spec-doc", json={"content": "# 需求文档"})
    assert resp.status_code == 200
    assert resp.json()["revision"] == 2

    resp = await client.get("/api/tasks/1/spec-doc")
    assert resp.status_code == 200
    assert resp.json()["content"] == "# 需求文档"
    assert resp.json()["revision"] == 2


async def test_spec_doc_put_rejects_empty_content(client):
    resp = await client.put("/api/tasks/1/spec-doc", json={"content": ""})
    assert resp.status_code == 422


async def test_spec_doc_image_and_parse(client):
    resp = await client.post("/api/tasks/spec-doc/image")
    assert resp.status_code == 200
    assert "object_path" in resp.json()

    resp = await client.post("/api/tasks/spec-doc/parse")
    assert resp.status_code == 200
    assert resp.json()["content_type"] == "markdown"


async def test_operation_logs_empty(client):
    resp = await client.get("/api/tasks/1/operation-logs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_relations_roundtrip(client):
    resp = await client.post("/api/tasks/1/relations",
                             json={"target_task_id": 2, "relation_type": "predecessor"})
    assert resp.status_code == 200
    assert resp.json()["source_task_id"] == 1

    resp = await client.get("/api/tasks/1/relations")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await client.get("/api/tasks/1/relations/tree")
    assert resp.status_code == 200
    assert resp.json()["task_id"] == 1


async def test_relations_post_requires_target(client):
    resp = await client.post("/api/tasks/1/relations", json={"relation_type": "duplicate"})
    assert resp.status_code == 422


async def test_wechat_readonly_routes(client):
    resp = await client.get("/api/wechat/permissions")
    assert resp.status_code == 200
    assert "permissions" in resp.json()

    resp = await client.get("/api/wechat/get-openid?code=test")
    assert resp.status_code == 200
    assert resp.json()["openid"] == "mock_openid"

    resp = await client.get("/api/wechat/config/js-sdk-config")
    assert resp.status_code == 200
    assert "signature" in resp.json()

    resp = await client.post("/api/wechat/broadcast_message", json={})
    assert resp.status_code == 200
    assert resp.json()["errcode"] == 0
