'''Custom logging handlers for console, file, and Allure output.'''

import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ANSI color codes
_COLORS = {
    'DEBUG': '\033[36m',       # Cyan
    'INFO': '\033[32m',        # Green
    'WARNING': '\033[33m',     # Yellow
    'ERROR': '\033[31m',       # Red
    'CRITICAL': '\033[35m',    # Magenta
}
_RESET = '\033[0m'


class ConsoleColorHandler(logging.StreamHandler):
    '''Stream handler with optional ANSI color support.'''

    def __init__(self, use_colors: bool = True, stream=None):
        super().__init__(stream or sys.stdout)
        self._use_colors = use_colors
        self._configure_formatter(use_colors)

    def _configure_formatter(self, use_colors: bool):
        if use_colors:
            fmt = '%(asctime)s | %(color_level)-8s | %(name)s | %(message)s'
        else:
            fmt = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
        datefmt = '%Y-%m-%d %H:%M:%S'
        self.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    def format(self, record: logging.LogRecord) -> str:
        if self._use_colors:
            color = _COLORS.get(record.levelname, _RESET)
            record.color_level = f'{color}{record.levelname}'
        return super().format(record)


class JsonFormatter(logging.Formatter):
    '''Format log records as JSON lines.'''

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'message': record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            obj['exception'] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


class RotatingFileHandler(logging.handlers.RotatingFileHandler):
    '''Rotating file handler that creates parent directories automatically.'''

    def __init__(self, file_path: str, max_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 5, fmt: str = 'json'):
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(path), maxBytes=max_bytes, backupCount=backup_count,
                         encoding='utf-8')
        if fmt == 'json':
            self.setFormatter(JsonFormatter())
        else:
            fmt = '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
            self.setFormatter(logging.Formatter(fmt, '%Y-%m-%d %H:%M:%S'))


class AllureLogHandler(logging.Handler):
    '''Log handler that attaches records to Allure report.

    Buffers log records per-thread and attaches them when flushed.
    Falls back gracefully if allure is not available.
    '''

    def __init__(self, level: int = logging.WARNING):
        super().__init__(level=level)
        self._local = threading.local()
        self._local.records = []
        self._allure_available = self._check_allure()

    @staticmethod
    def _check_allure() -> bool:
        try:
            import allure  # noqa: F401
            return True
        except ImportError:
            return False

    def emit(self, record: logging.LogRecord) -> None:
        if not self._allure_available:
            return
        self._local.records.append(self.format(record))

    def flush(self) -> None:
        if not self._allure_available or not self._local.records:
            return
        try:
            import allure
            records = self._local.records
            if records:
                log_text = '\n'.join(records)
                allure.attach(log_text, name='framework_log',
                              attachment_type=allure.attachment_type.TEXT)
        except Exception:
            pass
        finally:
            self._local.records = []

