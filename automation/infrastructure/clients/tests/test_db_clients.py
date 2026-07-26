from unittest.mock import MagicMock, patch

import pytest

from automation.infrastructure.config.models import DatabaseConfig, RedisConfig, QdrantConfig
from automation.infrastructure.clients.exceptions import ClientConnectionError
from automation.infrastructure.clients.mysql_client import MySQLClient
from automation.infrastructure.clients.qdrant_client import QdrantClient
from automation.infrastructure.clients.redis_client import RedisClient


class TestMySQLClient:
    def _make_mock_connect(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    @patch("pymysql.connect")
    def test_connect(self, mock_connect):
        conn, _ = self._make_mock_connect()
        mock_connect.return_value = conn
        client = MySQLClient(DatabaseConfig(host="test-db", database="testdb"))
        client.connect()
        assert client.is_connected is True
        client.close()

    @patch("pymysql.connect")
    def test_connect_sets_cursor(self, mock_connect):
        conn, cursor = self._make_mock_connect()
        mock_connect.return_value = conn
        client = MySQLClient(DatabaseConfig(host="test-db", database="testdb"))
        client.connect()
        assert client._cursor is not None
        client.close()

    def test_close(self):
        client = MySQLClient()
        client._connection = MagicMock()
        client._cursor = MagicMock()
        client._connected = True
        client.close()
        assert client.is_connected is False
        assert client._cursor is None
        assert client._connection is None

    def test_execute_without_connect_raises(self):
        client = MySQLClient()
        with pytest.raises(ClientConnectionError, match="MySQL not connected"):
            client.execute("SELECT 1")

    @patch("pymysql.connect")
    def test_execute_success(self, mock_connect):
        conn, cursor = self._make_mock_connect()
        cursor.rowcount = 1
        mock_connect.return_value = conn
        client = MySQLClient(DatabaseConfig(host="test-db", database="testdb"))
        client.connect()
        affected = client.execute("UPDATE test SET x=1")
        assert affected == 1
        client.close()

    @patch("pymysql.connect")
    def test_fetch_one(self, mock_connect):
        conn, cursor = self._make_mock_connect()
        cursor.rowcount = 1
        cursor.fetchone.return_value = {"id": 1, "name": "test"}
        mock_connect.return_value = conn
        client = MySQLClient()
        client._connection = conn
        client._cursor = cursor
        client._connected = True
        result = client.fetch_one("SELECT * FROM test WHERE id=1")
        assert result == {"id": 1, "name": "test"}


class TestRedisClient:
    def test_connect_missing_library(self):
        client = RedisClient(RedisConfig(host="test-redis"))
        with pytest.raises(ImportError, match="redis library"):
            client.connect()

    def test_connect_success(self):
        mock_redis_mod = MagicMock()
        mock_redis_instance = MagicMock()
        mock_redis_mod.Redis.return_value = mock_redis_instance
        with patch.dict("sys.modules", {"redis": mock_redis_mod}):
            client = RedisClient(RedisConfig(host="test-redis"))
            client.connect()
            assert client.is_connected is True
            client.close()

    def test_get_without_connect_raises(self):
        client = RedisClient()
        with pytest.raises(ClientConnectionError, match="Redis not connected"):
            client.get("test_key")

    def test_set_without_connect_raises(self):
        client = RedisClient()
        with pytest.raises(ClientConnectionError, match="Redis not connected"):
            client.set("key", "value")

    def test_delete_without_connect_raises(self):
        client = RedisClient()
        with pytest.raises(ClientConnectionError, match="Redis not connected"):
            client.delete("key")

    def test_exists_without_connect_raises(self):
        client = RedisClient()
        with pytest.raises(ClientConnectionError, match="Redis not connected"):
            client.exists("key")


class TestQdrantClient:
    def test_connect_missing_library(self):
        client = QdrantClient(QdrantConfig(host="test-qdrant"))
        with pytest.raises(ImportError, match="qdrant-client"):
            client.connect()

    def test_connect_success(self):
        mock_lib = MagicMock()
        mock_client = MagicMock()
        mock_lib.QdrantClient.return_value = mock_client
        with patch.dict("sys.modules", {"qdrant_client": mock_lib}):
            client = QdrantClient(QdrantConfig(host="test-qdrant"))
            client.connect()
            assert client.is_connected is True
            client.close()

    def test_search_without_connect_raises(self):
        client = QdrantClient()
        with pytest.raises(ClientConnectionError, match="Qdrant not connected"):
            client.search("test_collection", [0.1, 0.2])

    def test_upsert_without_connect_raises(self):
        client = QdrantClient()
        with pytest.raises(ClientConnectionError, match="Qdrant not connected"):
            client.upsert("test_collection", [])

    def test_close(self):
        mock_client = MagicMock()
        client = QdrantClient()
        client._client = mock_client
        client._connected = True
        client.close()
        assert client.is_connected is False
        assert client._client is None
        mock_client.close.assert_called_once()

    def test_collection_exists(self):
        mock_client = MagicMock()
        client = QdrantClient()
        client._client = mock_client
        assert client.collection_exists("test") is True
        mock_client.get_collection.assert_called_with("test")

    def test_collection_not_exists(self):
        mock_client = MagicMock()
        mock_client.get_collection.side_effect = Exception("not found")
        client = QdrantClient()
        client._client = mock_client
        assert client.collection_exists("test") is False

