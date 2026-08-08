"""Shared test fixtures: mock backend, auth, and shared infrastructure fixtures."""
import os
import pytest
from automation.config import load_config
from automation.config.models import ApiConfig
from automation.src.clients.api_client import ApiClient
from automation.src.mocks.backend_mock import create_mock_transport
from automation.src.logger.handlers import AllureLogHandler
from automation.src.fixtures import (
    api_client,
    config,
    config_env,
    log_config,
    logger,
    mysql_client,
    qdrant_client,
    redis_client,
    setup_logger,
)
import logging

@pytest.fixture
async def mock_api_client():
    use_mock = os.getenv("USE_MOCK", "1") != "0"
    if use_mock:
        client = ApiClient(
            config=ApiConfig(base_url="http://mock.local", timeout=30),
            transport=create_mock_transport(),
            raise_auth_errors=False,
        )
    else:
        client = ApiClient(config=load_config().api, raise_auth_errors=False)
    await client.connect()
    yield client
    await client.close()

@pytest.fixture
async def mock_auth_token(mock_api_client):
    r = await mock_api_client.post("/api/auth/login", json={"username":"testadmin","password":"admin123"})
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
