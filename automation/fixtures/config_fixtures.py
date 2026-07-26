import pytest
from typing import Generator

from automation.config import AutomationConfig, ConfigEnv, load_config


@pytest.fixture(scope="session")
def config() -> AutomationConfig:
    """Return AutomationConfig loaded once per test session.

    Uses AUTOMATION_ENV environment variable or defaults to 'local'.
    """
    return load_config()


@pytest.fixture(scope="session")
def config_env(config: AutomationConfig) -> ConfigEnv:
    """Return the current environment as a ConfigEnv enum."""
    return ConfigEnv.from_str(config.env)


@pytest.fixture(scope="session")
def log_config() -> str:
    """Return the active environment name string."""
    config_obj = load_config()
    return config_obj.env
