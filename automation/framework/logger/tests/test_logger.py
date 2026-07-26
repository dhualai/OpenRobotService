'''Tests for the unified logging module.'''

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest

from automation.framework.logger import LogConfig, get_logger, reset_logging, setup_logging
from automation.framework.logger.handlers import ConsoleColorHandler


@pytest.fixture(autouse=True)
def reset_logging_before_test():
    reset_logging()
    yield
    reset_logging()


class TestSetupLogging:
    '''Test the setup_logging function.'''

    def test_setup_defaults(self):
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_setup_twice_idempotent(self):
        setup_logging()
        count = len(logging.getLogger().handlers)
        setup_logging()
        assert len(logging.getLogger().handlers) == count

    def test_setup_only_console(self):
        cfg = LogConfig(file_enabled=False, allure_enabled=False)
        setup_logging(cfg)
        root = logging.getLogger()
        names = [type(h).__name__ for h in root.handlers]
        assert 'ConsoleColorHandler' in names
        assert 'RotatingFileHandler' not in names

    def test_setup_different_level(self):
        cfg = LogConfig(level='DEBUG')
        setup_logging(cfg)
        assert logging.getLogger().level == logging.DEBUG


class TestGetLogger:
    '''Test the get_logger function.'''

    def test_get_logger_basic(self):
        log = get_logger('test.module')
        assert log.name == 'automation.test.module'

    def test_get_logger_already_prefixed(self):
        log = get_logger('automation.config')
        assert log.name == 'automation.config'

    def test_get_logger_configures_correctly(self):
        setup_logging()
        log = get_logger('test')
        assert isinstance(log, logging.Logger)


class TestConsoleHandler:
    '''Test console logging output.'''

    def test_console_creates_handler(self):
        setup_logging(LogConfig(file_enabled=False, allure_enabled=False))
        root = logging.getLogger()
        console_handlers = [h for h in root.handlers if isinstance(h, ConsoleColorHandler)]
        assert len(console_handlers) == 1

    def test_console_level_filtering(self):
        cfg = LogConfig(console_level='WARNING', file_enabled=False, allure_enabled=False)
        setup_logging(cfg)
        root = logging.getLogger()
        console = [h for h in root.handlers if isinstance(h, ConsoleColorHandler)][0]
        assert console.level == logging.WARNING


class TestFileHandler:
    '''Test file-based logging.'''

    def _write_and_verify(self, tmpdir, fmt='json'):
        log_path = str(Path(tmpdir) / f'test_{fmt}.log')
        cfg = LogConfig(
            file_enabled=True, file_path=log_path, file_format=fmt,
            console_enabled=False, allure_enabled=False,
        )
        setup_logging(cfg)
        log = get_logger('test_file')
        log.info('file log entry')
        # Close handler before cleanup
        reset_logging()
        assert os.path.exists(log_path)
        return log_path

    def test_file_creates_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_and_verify(tmpdir)
            content = open(path, encoding='utf-8').read()
            assert 'file log entry' in content

    def test_file_json_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_and_verify(tmpdir, fmt='json')
            line = open(path, encoding='utf-8').readline().strip()
            parsed = json.loads(line)
            assert parsed['level'] == 'INFO'
            assert parsed['message'] == 'file log entry'
            assert parsed['logger'] == 'automation.test_file'

    def test_file_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = str(Path(tmpdir) / 'nested' / 'logs' / 'deep.log')
            cfg = LogConfig(
                file_enabled=True, file_path=log_path,
                console_enabled=False, allure_enabled=False,
            )
            setup_logging(cfg)
            get_logger('test_dir').info('dir created')
            reset_logging()
            assert os.path.exists(log_path)


class TestAllureHandler:
    '''Test Allure log handler (without actual Allure runtime).'''

    def test_allure_handler_attached(self):
        cfg = LogConfig(console_enabled=False, file_enabled=False, allure_enabled=True)
        setup_logging(cfg)
        root = logging.getLogger()
        names = [type(h).__name__ for h in root.handlers]
        assert 'AllureLogHandler' in names

    def test_allure_handler_graceful(self):
        from automation.framework.logger.handlers import AllureLogHandler
        h = AllureLogHandler()
        assert h._allure_available is True
        record = logging.LogRecord('test', logging.ERROR, '', 0, 'test msg', None, None)
        try:
            h.emit(record)
        except Exception:
            pytest.fail('AllureLogHandler.emit raised unexpectedly')


class TestResetLogging:
    '''Test resetting logging.'''

    def test_reset_clears_handlers(self):
        setup_logging()
        assert len(logging.getLogger().handlers) > 0
        reset_logging()
        assert len(logging.getLogger().handlers) == 0

    def test_setup_after_reset_works(self):
        setup_logging()
        reset_logging()
        setup_logging()
        assert len(logging.getLogger().handlers) > 0

