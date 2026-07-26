import os
from pathlib import Path
from typing import Optional

from automation.config.enums import ConfigEnv
from automation.config.loader import ConfigLoader
from automation.config.models import AutomationConfig


_AUTOMATION_ENV_VAR = 'AUTOMATION_ENV'
_DEFAULT_ENV = 'local'


def _detect_env() -> str:
    '''Detect active environment from env var, default to local.'''
    return os.getenv(_AUTOMATION_ENV_VAR, _DEFAULT_ENV).lower()


def load_config(
    env: Optional[str] = None,
    profiles_dir: Optional[Path] = None,
) -> AutomationConfig:
    '''Load automation config for the given (or detected) environment.

    Args:
        env: Environment name (local/sit/uat). Defaults to AUTOMATION_ENV env var or 'local'.
        profiles_dir: Override profiles directory. Defaults to config/profiles/.

    Returns:
        Fully populated AutomationConfig instance.

    Raises:
        FileNotFoundError: If profile YAML for the environment does not exist.
    '''
    target_env = (env or _detect_env()).lower()
    loader = ConfigLoader(env=target_env, profiles_dir=profiles_dir)
    return loader.load()


def get_env() -> str:
    '''Get the current active environment name.'''
    return _detect_env()


def is_env(env: ConfigEnv) -> bool:
    '''Check if the current active environment matches the given env.'''
    return _detect_env() == env.value
