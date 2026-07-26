"""Tests for shared fixture definitions — tests fixture logic without calling fixtures directly."""

import pytest

from automation.config import ConfigEnv, load_config
from automation.logger import get_logger


@pytest.mark.usefixtures("setup_logger")
class TestConfigFixtures:
    def test_config_loaded(self, config):
        assert config.api.base_url is not None
        assert config.database.host is not None
        assert config.redis.host is not None
        assert config.qdrant.host is not None

    def test_config_env_logic(self, monkeypatch):
        """Test the underlying logic of config_env fixture (not the fixture itself)."""
        monkeypatch.setenv("AUTOMATION_ENV", "local")
        cfg = load_config()
        env = ConfigEnv.from_str(cfg.env)
        assert env.value == "local"

    def test_logger(self, logger):
        assert logger.name == "automation.test"


class TestClientFixtures:
    """Test that client classes can be instantiated with config."""

    def test_api_client_creates_with_config(self):
        from automation.clients import ApiClient
        cfg = load_config()
        client = ApiClient(config=cfg.api)
        assert client._cfg.base_url is not None

    def test_mysql_client_creates_with_config(self):
        from automation.clients import MySQLClient
        cfg = load_config()
        client = MySQLClient(config=cfg.database)
        assert client._cfg.host is not None

    def test_redis_client_creates_with_config(self):
        from automation.clients import RedisClient
        cfg = load_config()
        client = RedisClient(config=cfg.redis)
        assert client._cfg.host is not None

    def test_qdrant_client_creates_with_config(self):
        from automation.clients import QdrantClient
        cfg = load_config()
        client = QdrantClient(config=cfg.qdrant)
        assert client._cfg.host is not None
