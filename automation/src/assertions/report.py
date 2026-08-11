"""Assertion attachment aggregation.

Assertions record their expected/actual details into a per-test buffer instead
of attaching immediately; the caller (step wrapper) flushes the buffer once
per request step, so each request produces exactly one "断言信息" attachment.
"""

import contextvars
import json

import allure

_assert_buffer: contextvars.ContextVar = contextvars.ContextVar("assert_buffer", default=None)


def record(detail: dict) -> None:
    """Record one assertion detail into the current test's buffer."""
    buf = _assert_buffer.get()
    if buf is None:
        buf = []
        _assert_buffer.set(buf)
    buf.append(detail)


def flush_assert_attachment() -> None:
    """Attach all recorded assertion details as a single attachment and clear the buffer."""
    buf = _assert_buffer.get()
    if not buf:
        return
    try:
        allure.attach(
            json.dumps(buf, indent=2, ensure_ascii=False, default=str),
            name="断言信息",
            attachment_type=allure.attachment_type.JSON,
        )
    except Exception:
        pass
    _assert_buffer.set([])
