import pytest
import logging

from automation.logger import LogConfig, get_logger, setup_logging


@pytest.fixture(scope="session", autouse=True)
def setup_logger() -> None:
    """Auto-setup logging once per test session.

    Uses default LogConfig. Override by setting AUTOMATION_LOG_LEVEL env var.
    """
    setup_logging()
    yield


@pytest.fixture
def logger() -> logging.Logger:
    """Return a logger for the current test.

    The logger name is derived from the test module.
    """
    return get_logger("test")
