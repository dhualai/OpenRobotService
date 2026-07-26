"""DB test fixtures."""

import pytest
from unittest.mock import MagicMock

from automation.clients.mysql_client import MySQLClient
from automation.clients.redis_client import RedisClient
from automation.clients.qdrant_client import QdrantClient


@pytest.fixture
def mock_mysql_client() -> MySQLClient:
    return MagicMock(spec=MySQLClient)


@pytest.fixture
def mock_redis_client() -> RedisClient:
    return MagicMock(spec=RedisClient)


@pytest.fixture
def mock_qdrant_client() -> QdrantClient:
    return MagicMock(spec=QdrantClient)
