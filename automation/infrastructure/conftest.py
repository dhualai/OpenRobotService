def pytest_addoption(parser):
    parser.addoption('--offline', action='store_true', default=False,
        help='Skip tests that require real service connections')

# Root conftest for automation framework.
# Imports and re-exports all shared fixtures from framework/fixtures.
# These fixtures are auto-discovered by all tests under automation/.

from automation.infrastructure.fixtures import (
    api_client,
    config,
    config_env,
    log_config,
    logger,
    mysql_client,
    qdrant_client,
    redis_client,
    setup_logger,
)

__all__ = [
    "config",
    "config_env",
    "log_config",
    "logger",
    "setup_logger",
    "api_client",
    "mysql_client",
    "redis_client",
    "qdrant_client",
]

