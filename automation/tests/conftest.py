"""Shared test fixtures: mock backend, auth, and shared infrastructure fixtures."""
import os
import allure
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
def allure_test_category(request):
    """按测试文件路径给 Allure 增加顶层分类，便于区分链路和单接口用例。"""
    normalized = str(request.node.fspath).replace("\\", "/")
    if "/tests/business_chain/" in normalized or normalized.endswith("/tests/real/test_ticket_lifecycle_real.py"):
        allure.dynamic.parent_suite("场景用例")
        allure.dynamic.suite("真实后端工单生命周期" if normalized.endswith("/tests/real/test_ticket_lifecycle_real.py") else "完整业务链路")
    elif "/tests/" in normalized:
        module_name = getattr(request.node.module, "__name__", "单接口用例").split(".")[-1]
        allure.dynamic.parent_suite("单接口用例")
        allure.dynamic.suite(module_name)
    yield

@pytest.fixture(autouse=True)
def allure_flush():
    yield
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, AllureLogHandler):
            h.flush()
