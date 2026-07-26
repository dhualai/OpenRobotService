import pytest
from typing import AsyncGenerator, Generator

from automation.config import AutomationConfig
from automation.clients.api_client import ApiClient
from automation.clients.mysql_client import MySQLClient
from automation.clients.qdrant_client import QdrantClient
from automation.clients.redis_client import RedisClient
from automation.fixtures.config_fixtures import config


@pytest.fixture
async def api_client(config: AutomationConfig) -> AsyncGenerator[ApiClient, None]:
    """Return a connected ApiClient for the test (async).

    Skips if connection fails (e.g., backend not running).
    """
    client = ApiClient(config=config.api)
    try:
        await client.connect()
    except Exception as e:
        pytest.skip(f"ApiClient connection failed: {e}")
    yield client
    await client.close()


@pytest.fixture
def mysql_client(config: AutomationConfig) -> Generator[MySQLClient, None, None]:
    """Return a connected MySQLClient for the test.

    Skips if connection fails (e.g., MySQL not running).
    """
    client = MySQLClient(config=config.database)
    try:
        client.connect()
    except Exception as e:
        pytest.skip(f"MySQLClient connection failed: {e}")
    yield client
    client.close()


@pytest.fixture
def redis_client(config: AutomationConfig) -> Generator[RedisClient, None, None]:
    """Return a connected RedisClient for the test.

    Skips if connection fails (e.g., Redis not running or library not installed).
    """
    client = RedisClient(config=config.redis)
    try:
        client.connect()
    except (ImportError, Exception) as e:
        pytest.skip(f"RedisClient connection failed: {e}")
    yield client
    client.close()


@pytest.fixture
def qdrant_client(config: AutomationConfig) -> Generator[QdrantClient, None, None]:
    """Return a connected QdrantClient for the test.

    Skips if connection fails (e.g., Qdrant not running or library not installed).
    """
    client = QdrantClient(config=config.qdrant)
    try:
        client.connect()
    except (ImportError, Exception) as e:
        pytest.skip(f"QdrantClient connection failed: {e}")
    yield client
    client.close()
