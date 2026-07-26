'''Core logging setup: factory functions for configuring and obtaining loggers.'''

import logging
from typing import Optional

from automation.logger.config import LogConfig
from automation.logger.handlers import (
    AllureLogHandler,
    ConsoleColorHandler,
    RotatingFileHandler,
)


_initialized = False


def setup_logging(config: Optional[LogConfig] = None) -> None:
    '''Configure the root logger with console, file, and Allure handlers.

    Args:
        config: LogConfig instance. Uses defaults if None.

    This function is idempotent after the first call.
    '''
    global _initialized
    if _initialized:
        return

    cfg = config or LogConfig()
    root = logging.getLogger()

    # Set root level
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))

    # Console handler
    if cfg.console_enabled:
        console = ConsoleColorHandler(use_colors=cfg.console_use_colors)
        console.setLevel(getattr(logging, cfg.console_level.upper(), logging.DEBUG))
        root.addHandler(console)

    # File handler
    if cfg.file_enabled and cfg.file_path:
        fh = RotatingFileHandler(
            file_path=cfg.file_path,
            max_bytes=cfg.file_max_bytes,
            backup_count=cfg.file_backup_count,
            fmt=cfg.file_format,
        )
        fh.setLevel(getattr(logging, cfg.file_level.upper(), logging.DEBUG))
        root.addHandler(fh)

    # Allure handler
    if cfg.allure_enabled:
        ah = AllureLogHandler(
            level=getattr(logging, cfg.allure_level.upper(), logging.WARNING),
        )
        root.addHandler(ah)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    '''Get a configured logger instance.

    Args:
        name: Logger name, typically __name__ of the calling module.
              Will be prefixed with 'automation.' if not already.

    Returns:
        Configured Logger instance.
    '''
    if not name.startswith('automation'):
        name = f'automation.{name}'
    return logging.getLogger(name)


def reset_logging() -> None:
    '''Reset all logging configuration. Useful for testing.'''
    global _initialized
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    _initialized = False

