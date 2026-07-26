"""API test fixtures: auth_token, api_client, cleanup."""

import pytest
import logging

from automation.config import load_config
from automation.clients.api_client import ApiClient
from automation.logger.handlers import AllureLogHandler


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



# ===== Mock backend fixtures =====

@pytest.fixture
async def mock_api_client():
    from automation.config.models import ApiConfig
    from automation.clients.api_client import ApiClient
    import httpx
    from automation.mocks.backend_mock import create_mock_transport
    transport = create_mock_transport()
    config = ApiConfig(base_url="http://test")
    client = ApiClient(config=config)
    client._client = httpx.AsyncClient(transport=transport, base_url=config.base_url)
    client._connected = True
    yield client
    await client.close()


@pytest.fixture
async def mock_auth_token(mock_api_client):
    login_data = {'username': 'testadmin', 'password': 'admin123'}
    response = await mock_api_client.post('/auth/login', json=login_data)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def mock_auth_header(mock_auth_token):
    return {'Authorization': 'Bearer ' + mock_auth_token['access_token']}


