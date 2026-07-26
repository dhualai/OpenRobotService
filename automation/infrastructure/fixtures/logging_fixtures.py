import pytest
import logging

from automation.infrastructure.logger import LogConfig, get_logger, setup_logging
from automation.infrastructure.logger.handlers import AllureLogHandler


@pytest.fixture(scope="session", autouse=True)
def setup_logger() -> None:
    """Auto-setup logging once per test session."""
    setup_logging()
    yield


@pytest.fixture(autouse=True)
def allure_log_flush():
    """Flush AllureLogHandler after each test to attach logs to Allure report."""
    yield
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, AllureLogHandler):
            h.flush()


@pytest.fixture
def logger() -> logging.Logger:
    """Return a logger for the current test."""
    return get_logger("test")
