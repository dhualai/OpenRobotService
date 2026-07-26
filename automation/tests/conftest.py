"""Shared test fixtures: mock backend and auth."""
import pytest
import httpx
from automation.infrastructure.mocks.backend_mock import create_mock_transport
from automation.infrastructure.logger.handlers import AllureLogHandler
import logging

@pytest.fixture
async def mock_api_client():
    transport = create_mock_transport()
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest.fixture
async def mock_auth_token(mock_api_client):
    r = await mock_api_client.post("/auth/login", json={"username":"testadmin","password":"admin123"})
    return r.json()["access_token"]

@pytest.fixture
def mock_auth_header(mock_auth_token):
    return {"Authorization": f"Bearer {mock_auth_token}"}

@pytest.fixture(autouse=True)
def allure_flush():
    yield
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, AllureLogHandler):
            h.flush()
