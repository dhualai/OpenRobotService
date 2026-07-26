import time
from typing import Any, Callable

import pytest


def assert_max_duration(operation: Callable[[], Any], max_ms: float) -> Any:
    """Assert operation completes within max_ms milliseconds.

    Args:
        operation: Zero-argument callable to time.
        max_ms: Maximum allowed duration in milliseconds.

    Returns:
        Return value of the operation.
    """
    start = time.perf_counter()
    result = operation()
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms > max_ms:
        pytest.fail(f"Operation took {elapsed_ms:.1f}ms, expected max {max_ms:.1f}ms")
    return result


def assert_min_duration(operation: Callable[[], Any], min_ms: float) -> Any:
    """Assert operation takes at least min_ms milliseconds."""
    start = time.perf_counter()
    result = operation()
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms < min_ms:
        pytest.fail(f"Operation took {elapsed_ms:.1f}ms, expected min {min_ms:.1f}ms")
    return result


def assert_duration_between(operation: Callable[[], Any], min_ms: float, max_ms: float) -> Any:
    """Assert operation duration is between min_ms and max_ms."""
    start = time.perf_counter()
    result = operation()
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms < min_ms or elapsed_ms > max_ms:
        pytest.fail(f"Operation took {elapsed_ms:.1f}ms, expected between {min_ms:.1f}ms and {max_ms:.1f}ms")
    return result
