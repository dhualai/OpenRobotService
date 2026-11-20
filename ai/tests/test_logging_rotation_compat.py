import importlib.util
import logging.handlers
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_backend_logging = _load_module(
    "test_backend_logging_module",
    "backend/app/services/logging.py",
)
_ai_logging = _load_module(
    "test_ai_core_logging_module",
    "ai/core/logging.py",
)
_data_analysis_logging = _load_module(
    "test_data_analysis_logging_module",
    "ai/agents/AiDataAnalysisPlatform/logging_config.py",
)


@pytest.mark.parametrize(
    ("handler_cls", "base_cls"),
    [
        (_backend_logging._WindowsSafeRotatingHandler, logging.handlers.TimedRotatingFileHandler),
        (_ai_logging._WindowsSafeTimedRotatingFileHandler, logging.handlers.TimedRotatingFileHandler),
        (_data_analysis_logging._WindowsSafeRotatingFileHandler, logging.handlers.RotatingFileHandler),
    ],
)
def test_safe_handlers_keep_standard_rollover_on_linux(monkeypatch, handler_cls, base_cls):
    calls = []

    def fake_rollover(self):
        calls.append(self)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(base_cls, "doRollover", fake_rollover)

    handler = object.__new__(handler_cls)
    handler.doRollover()

    assert calls == [handler]


@pytest.mark.parametrize(
    ("handler_cls", "base_cls"),
    [
        (_backend_logging._WindowsSafeRotatingHandler, logging.handlers.TimedRotatingFileHandler),
        (_ai_logging._WindowsSafeTimedRotatingFileHandler, logging.handlers.TimedRotatingFileHandler),
        (_data_analysis_logging._WindowsSafeRotatingFileHandler, logging.handlers.RotatingFileHandler),
    ],
)
def test_safe_handlers_ignore_permission_error_on_windows(monkeypatch, handler_cls, base_cls):
    class DummyStream:
        closed = False

        def close(self):
            self.closed = True

    def fail_rollover(self):
        raise PermissionError("locked")

    reopened_stream = object()
    stream = DummyStream()

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(base_cls, "doRollover", fail_rollover)

    handler = object.__new__(handler_cls)
    handler.stream = stream
    handler.mode = "w"
    handler._open = lambda: reopened_stream

    handler.doRollover()

    assert stream.closed is True
    assert handler.mode == "a"
    assert handler.stream is reopened_stream
