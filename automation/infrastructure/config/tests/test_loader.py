'''Tests for config.loader module.'''

import os
from pathlib import Path
import tempfile

import pytest
import yaml

from automation.infrastructure.config.enums import ConfigEnv
from automation.infrastructure.config.loader import ConfigLoader
from automation.infrastructure.config.models import AutomationConfig


class TestConfigLoader:
    '''Test ConfigLoader YAML loading and resolution.'''

    @pytest.fixture
    def temp_profiles(self):
        '''Create temporary profiles for testing.'''
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_dir = Path(tmpdir)
            profile = {'api': {'base_url': 'http://test.local:8000', 'timeout': 15}}
            with open(profiles_dir / 'testenv.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(profile, f)
            yield profiles_dir

    def test_load_profile(self, temp_profiles):
        loader = ConfigLoader(env='testenv', profiles_dir=temp_profiles)
        config = loader.load()
        assert isinstance(config, AutomationConfig)
        assert config.env == 'testenv'
        assert config.api.base_url == 'http://test.local:8000'
        assert config.api.timeout == 15

    def test_load_raw(self, temp_profiles):
        loader = ConfigLoader(env='testenv', profiles_dir=temp_profiles)
        raw = loader.load_raw()
        assert isinstance(raw, dict)
        assert raw['api']['base_url'] == 'http://test.local:8000'

    def test_load_profile_not_found(self):
        loader = ConfigLoader(env='nonexistent')
        with pytest.raises(FileNotFoundError, match='Config profile not found'):
            loader.load()

    def test_get_config_caches(self, temp_profiles):
        loader = ConfigLoader(env='testenv', profiles_dir=temp_profiles)
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

    def test_load_with_defaults_for_missing_fields(self, temp_profiles):
        '''Loading a partial profile should use defaults for missing sections.'''
        profiles_dir = temp_profiles.parent / 'partial_profiles'
        profiles_dir.mkdir(exist_ok=True)
        with open(profiles_dir / 'minimal.yaml', 'w', encoding='utf-8') as f:
            yaml.dump({'api': {'base_url': 'http://minimal.test'}}, f)
        loader = ConfigLoader(env='minimal', profiles_dir=profiles_dir)
        config = loader.load()
        assert config.api.base_url == 'http://minimal.test'
        assert config.database.host == 'localhost'
        assert config.redis.host == 'localhost'
        assert config.qdrant.host == 'localhost'

