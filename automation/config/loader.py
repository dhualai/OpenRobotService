import os
from pathlib import Path
from typing import Optional

import yaml

from automation.config.enums import ConfigEnv
from automation.config.models import AutomationConfig


class ConfigLoader:
    '''Load YAML config profiles by environment.'''

    def __init__(self, env: Optional[str] = None, profiles_dir: Optional[Path] = None):
        self._env = (env or os.getenv('AUTOMATION_ENV', 'local')).lower()
        self._profiles_dir = profiles_dir or (Path(__file__).parent / 'profiles')
        self._raw: dict = {}
        self._config: Optional[AutomationConfig] = None

    @property
    def env(self) -> str:
        return self._env

    def resolve_env(self) -> ConfigEnv:
        return ConfigEnv.from_str(self._env)

    def load_raw(self) -> dict:
        '''Load raw YAML dict from the environment profile file.'''
        path = self._find_profile()
        with open(path, 'r', encoding='utf-8') as f:
            self._raw = yaml.safe_load(f) or {}
        return self._raw

    def load(self) -> AutomationConfig:
        '''Load and parse config into typed AutomationConfig.'''
        raw = self.load_raw()
        known_fields = AutomationConfig.model_fields.keys()
        self._config = AutomationConfig(
            env=self._env,
            **{k: v for k, v in raw.items() if k in known_fields}
        )
        return self._config

    def get_config(self) -> AutomationConfig:
        '''Return cached config, loading if not yet loaded.'''
        if self._config is None:
            return self.load()
        return self._config

    def _find_profile(self) -> Path:
        path = self._profiles_dir / f'{self._env}.yaml'
        if not path.exists():
            valid = [e.value for e in ConfigEnv]
            raise FileNotFoundError(
                f'Config profile not found: {path}. '
                f'Valid environments: {valid}'
            )
        return path
