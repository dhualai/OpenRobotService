"""General-purpose helper utilities for tests."""

import os
import random
import string
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def unique_id(prefix: str = "test", length: int = 8) -> str:
    """Generate a unique identifier for test resources.

    Uses timestamp + random suffix to ensure uniqueness.

    Args:
        prefix: String prefix (default: "test").
        length: Length of random suffix (default: 8).

    Returns:
        String like "test_20260726_a1b2c3d4".
    """
    date = datetime.now().strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"{prefix}_{date}_{suffix}"


def generate_test_name(base: str = "test_data") -> str:
    """Generate a unique name for test data records.

    Args:
        base: Base name (default: "test_data").

    Returns:
        String like "test_data_20260726_a1b2".
    """
    return unique_id(prefix=base, length=4)


def normalize_path(path: str) -> str:
    """Normalize a file path to use OS-appropriate separators.

    On Windows: replaces forward slashes with backslashes.
    On Unix: keeps forward slashes.

    Args:
        path: File path string.

    Returns:
        Normalized path string.
    """
    return os.path.normpath(path)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries.

    Values in override take precedence over base.
    Nested dicts are merged recursively.

    Args:
        base: Base dictionary.
        override: Override dictionary.

    Returns:
        New merged dictionary (original dicts are not modified).
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def wait_for_condition(
    condition: Callable[[], Optional[T]],
    timeout: float = 10.0,
    interval: float = 0.5,
    description: str = "condition",
) -> T:
    """Poll until a condition function returns a truthy value.

    Args:
        condition: Zero-argument callable that returns a truthy value when met.
        timeout: Maximum wait time in seconds (default: 10.0).
        interval: Poll interval in seconds (default: 0.5).
        description: Description of the condition for error messages.

    Returns:
        The truthy value returned by the condition function.

    Raises:
        TimeoutError: If the condition is not met within the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"Timed out after {timeout}s waiting for: {description}")


def retry_on_exception(
    func: Callable[[], T],
    max_attempts: int = 3,
    exceptions: Tuple[type, ...] = (Exception,),
    delay: float = 0.5,
    backoff: float = 2.0,
) -> T:
    """Retry a function if it raises a specified exception.

    Simpler alternative to automation.utils.retry for non-decorator use.

    Args:
        func: Zero-argument callable to retry.
        max_attempts: Maximum retry attempts (default: 3).
        exceptions: Tuple of exception types that trigger retry (default: Exception).
        delay: Initial delay in seconds (default: 0.5).
        backoff: Delay multiplier per attempt (default: 2.0).

    Returns:
        Return value of func.

    Raises:
        The last exception if all attempts are exhausted.
    """
    last_exc = None
    current_delay = delay
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except exceptions as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(current_delay)
                current_delay *= backoff
    raise last_exc  # type: ignore
