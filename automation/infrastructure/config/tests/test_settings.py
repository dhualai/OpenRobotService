'''Tests for config.settings module (public API).'''

import os
from pathlib import Path
import tempfile

import pytest
import yaml

from automation.infrastructure.config import load_config, get_env, is_env, ConfigEnv


class TestLoadConfig:
    '''Test the public load_config() API.'''

    @pytest.fixture
    def temp_profiles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            data = {
                'api': {'base_url': 'http://custom.test', 'timeout': 99},
                'database': {'host': 'test-db'},
            }
            with open(p / 'custom.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(data, f)
            yield p

    def test_load_config_with_env(self, temp_profiles):
        config = load_config(env='custom', profiles_dir=temp_profiles)
        assert config.env == 'custom'
        assert config.api.base_url == 'http://custom.test'
        assert config.api.timeout == 99

    def test_load_config_uses_env_var(self, temp_profiles, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'custom')
        config = load_config(profiles_dir=temp_profiles)
        assert config.env == 'custom'

    def test_load_config_defaults_to_local(self, monkeypatch):
        monkeypatch.delenv('AUTOMATION_ENV', raising=False)
        config = load_config()
        assert config.env == 'local'
        assert config.api.base_url == 'http://localhost:8000'

    def test_load_config_with_env_override(self, temp_profiles, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'local')
        config = load_config(env='custom', profiles_dir=temp_profiles)
        assert config.env == 'custom'

    def test_load_config_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config(env='nonexistent')

    def test_load_config_returns_automation_config(self):
        config = load_config()
        assert config.api is not None
        assert config.database is not None
        assert config.redis is not None
        assert config.qdrant is not None
        assert config.deepseek is not None
        assert config.wechat is not None
        assert config.playwright is not None


class TestGetEnv:
    '''Test get_env() helper.'''

    def test_get_env_default(self, monkeypatch):
        monkeypatch.delenv('AUTOMATION_ENV', raising=False)
        assert get_env() == 'local'

    def test_get_env_from_var(self, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'sit')
        assert get_env() == 'sit'

    def test_get_env_case_insensitive(self, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'UAT')
        assert get_env() == 'uat'


class TestIsEnv:
    '''Test is_env() helper.'''

    def test_is_env_match(self, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'sit')
        assert is_env(ConfigEnv.SIT) is True
        assert is_env(ConfigEnv.LOCAL) is False

    def test_is_env_default(self, monkeypatch):
        monkeypatch.delenv('AUTOMATION_ENV', raising=False)
        assert is_env(ConfigEnv.LOCAL) is True
        assert is_env(ConfigEnv.SIT) is False

