"""Real backend smoke tests (USE_MOCK=0).

These tests hit the actual FastAPI service and are skipped by default
because the mock backend is the fast lane. Run them explicitly against a
running backend:

    USE_MOCK=0 pytest automation/tests/real -m smoke
"""

import os

import pytest

from automation.config.models import ApiConfig
from automation.src.assertions import assert_dict_contains_subset, assert_status_code
from automation.src.clients.api_client import ApiClient

pytestmark = [pytest.mark.smoke, pytest.mark.api]

_REAL_BASE_URL = os.getenv("REAL_API_BASE_URL", "http://localhost:8400")
_ADMIN_USER = os.getenv("REAL_ADMIN_USERNAME", "admin")
_ADMIN_PASSWORD = os.getenv("REAL_ADMIN_PASSWORD", "usp2026@EP")


@pytest.fixture
async def real_api_client():
    if os.getenv("USE_MOCK", "1") != "0":
        pytest.skip("USE_MOCK=0 required; mock backend is the default fast lane")
    client = ApiClient(
        config=ApiConfig(base_url=_REAL_BASE_URL, timeout=30),
        raise_auth_errors=False,
    )
    await client.connect()
    yield client
    await client.close()


async def test_health(real_api_client):
    r = await real_api_client.get("/api/health")
    assert_status_code(r, 200)
    assert_dict_contains_subset(r.json(), {"status": "healthy"})


async def test_admin_login_and_me(real_api_client):
    r = await real_api_client.post(
        "/api/auth/login",
        json={"username": _ADMIN_USER, "password": _ADMIN_PASSWORD},
    )
    assert_status_code(r, 200)
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await real_api_client.get("/api/auth/me", headers=headers)
    assert_status_code(me, 200)
    assert me.json()["username"] == _ADMIN_USER


async def test_task_create_and_get(real_api_client):
    login = await real_api_client.post(
        "/api/auth/login",
        json={"username": _ADMIN_USER, "password": _ADMIN_PASSWORD},
    )
    assert_status_code(login, 200)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    title = "real-smoke-task"
    created = await real_api_client.post(
        "/api/tasks",
        headers=headers,
        json={"title": title, "description": "real backend smoke"},
    )
    assert_status_code(created, 200)
    task_id = created.json()["id"]
    detail = await real_api_client.get(f"/api/tasks/{task_id}", headers=headers)
    assert_status_code(detail, 200)
    assert_dict_contains_subset(detail.json(), {"id": task_id, "title": title})