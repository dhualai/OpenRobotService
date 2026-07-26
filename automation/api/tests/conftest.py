"""API test fixtures: auth_token, api_client, cleanup."""

import pytest
import logging

from automation.config import load_config
from automation.clients.api_client import ApiClient
from automation.framework.logger.handlers import AllureLogHandler


@pytest.fixture
async def api_client():
    """Return a connected ApiClient for API tests."""
    client = ApiClient()
    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"ApiClient connection failed: {e}")
    yield client
    await client.close()


@pytest.fixture
async def auth_token(api_client):
    """Login with test user and return JWT token dict.

    Returns: {"access_token": str, "token_type": str, "refresh_token": str}
    """
    from automation.assertions import assert_status_code

    login_data = {
        "username": "testadmin",
        "password": "admin123",
    }
    response = await api_client.request("POST", "/auth/login", json=login_data)
    assert_status_code(response, 200)
    data = response.json()
    return data


@pytest.fixture
def auth_header(auth_token):
    """Return Authorization header dict for authenticated requests."""
    return {"Authorization": 'Bearer ' + auth_token['access_token']}


@pytest.fixture(autouse=True)
def allure_flush():
    """Flush AllureLogHandler after each test to ensure logs are attached."""
    yield
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, AllureLogHandler):
            h.flush()

