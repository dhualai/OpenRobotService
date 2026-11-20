import os
from pathlib import Path
from typing import Optional

import yaml

from automation.config.enums import ConfigEnv
from automation.config.models import AutomationConfig


class ConfigLoader:
    '''Load YAML config files by environment.

    Layout: <config_dir>/{env}/config.yaml (e.g. config/sit/config.yaml).
    '''

    def __init__(self, env: Optional[str] = None, config_dir: Optional[Path] = None):
        self._env = (env or os.getenv('AUTOMATION_ENV', 'local')).lower()
        self._config_dir = config_dir or Path(__file__).resolve().parent
        self._raw: dict = {}
        self._config: Optional[AutomationConfig] = None

    @property
    def env(self) -> str:
        return self._env

    def resolve_env(self) -> ConfigEnv:
        return ConfigEnv.from_str(self._env)

    def load_raw(self) -> dict:
        '''Load raw YAML dict from the environment config file.'''
        path = self._find_config_file()
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

    def _find_config_file(self) -> Path:
        path = self._config_dir / self._env / 'config.yaml'
        if not path.exists():
            valid = [e.value for e in ConfigEnv]
            raise FileNotFoundError(
                f'Config profile not found: {path}. '
                f'Valid environments: {valid}'
            )
        return path

