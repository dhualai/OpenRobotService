"""Shared fixtures for tests under infrastructure/.

Re-exports all fixtures from infrastructure/fixtures so they are
auto-discovered by pytest without explicit imports in test files.
"""

import pytest

from automation.src.fixtures import (
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


def pytest_addoption(parser):
    parser.addoption('--offline', action='store_true', default=False,
        help='Skip tests that require real service connections')


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
