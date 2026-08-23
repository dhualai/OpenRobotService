'''Tests for config.loader module.'''

import os
from pathlib import Path
import tempfile

import pytest
import yaml

from automation.config.enums import ConfigEnv
from automation.config.loader import ConfigLoader
from automation.config.models import AutomationConfig


def _write_env_config(config_dir: Path, env: str, data: dict) -> Path:
    env_dir = config_dir / env
    env_dir.mkdir(parents=True, exist_ok=True)
    path = env_dir / 'config.yaml'
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    return path


class TestConfigLoader:
    '''Test ConfigLoader YAML loading and resolution.'''

    @pytest.fixture
    def temp_config_dir(self):
        '''Create temporary config root (config/{env}/config.yaml layout).'''
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            data = {'api': {'base_url': 'http://test.local:8000', 'timeout': 15}}
            _write_env_config(config_dir, 'testenv', data)
            yield config_dir

    def test_load_profile(self, temp_config_dir):
        loader = ConfigLoader(env='testenv', config_dir=temp_config_dir)
        config = loader.load()
        assert isinstance(config, AutomationConfig)
        assert config.env == 'testenv'
        assert config.api.base_url == 'http://test.local:8000'
        assert config.api.timeout == 15

    def test_load_raw(self, temp_config_dir):
        loader = ConfigLoader(env='testenv', config_dir=temp_config_dir)
        raw = loader.load_raw()
        assert isinstance(raw, dict)
        assert raw['api']['base_url'] == 'http://test.local:8000'

    def test_load_profile_not_found(self):
        loader = ConfigLoader(env='nonexistent')
        with pytest.raises(FileNotFoundError, match='Config profile not found'):
            loader.load()

    def test_get_config_caches(self, temp_config_dir):
        loader = ConfigLoader(env='testenv', config_dir=temp_config_dir)
        c1 = loader.get_config()
        c2 = loader.get_config()
        assert c1 is c2

    def test_env_property(self):
        loader = ConfigLoader(env='uat')
        assert loader.env == 'uat'

    def test_detect_env_from_env_var(self, monkeypatch):
        monkeypatch.setenv('AUTOMATION_ENV', 'sit')
        loader = ConfigLoader()
        assert loader.env == 'sit'

    def test_default_env(self, monkeypatch):
        monkeypatch.delenv('AUTOMATION_ENV', raising=False)
        loader = ConfigLoader()
        assert loader.env == 'local'

    def test_resolve_env(self):
        loader = ConfigLoader(env='SIT')
        assert loader.resolve_env() == ConfigEnv.SIT

    def test_load_with_defaults_for_missing_fields(self, temp_config_dir):
        '''Loading a partial config should use defaults for missing sections.'''
        _write_env_config(temp_config_dir, 'minimal', {'api': {'base_url': 'http://minimal.test'}})
        loader = ConfigLoader(env='minimal', config_dir=temp_config_dir)
        config = loader.load()
        assert config.api.base_url == 'http://minimal.test'
        assert config.database.host == 'localhost'
        assert config.redis.host == 'localhost'
        assert config.qdrant.host == 'localhost'
