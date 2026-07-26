'''automation.logger - Unified logging module.

Provides logging infrastructure with support for:
- Console output (with ANSI colors)
- Rotating file output (JSON or plain text)
- Allure report attachment (for test reporting)
- Native pytest integration (via caplog)

Usage:
    from automation.logger import setup_logging, get_logger

    setup_logging()
    log = get_logger(__name__)
    log.info('Test step starting...')
'''

from automation.logger.config import LogConfig
from automation.logger.core import get_logger, reset_logging, setup_logging

__all__ = [
    'setup_logging',
    'get_logger',
    'reset_logging',
    'LogConfig',
]

