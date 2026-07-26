from typing import Any, Dict, List, Optional

import pytest


def assert_equals(actual: Any, expected: Any, msg: str = "") -> None:
    """Assert two values are equal with descriptive message."""
    if actual != expected:
        detail = msg or f"Expected {expected!r}, got {actual!r}"
        pytest.fail(detail)


def assert_not_empty(value: Any, msg: str = "") -> None:
    """Assert value is not empty."""
    if not value:
        detail = msg or f"Expected non-empty value, got {value!r}"
        pytest.fail(detail)


def assert_contains(container: Any, expected: Any, path: str = "") -> None:
    """Assert container contains expected value or key.

    Works with lists, dicts, and strings.
    """
    label = f" at {path}" if path else ""
    if isinstance(container, dict):
        if expected not in container:
            pytest.fail(f"Dict does not contain key {expected!r}{label}. Keys: {list(container.keys())[:20]}")
    elif isinstance(container, (list, tuple)):
        if expected not in container:
            pytest.fail(f"List does not contain {expected!r}{label}. Items: {container[:20]}")
    elif isinstance(container, str):
        if expected not in container:
            pytest.fail(f"String does not contain {expected!r}{label}. Text ({len(container)} chars)")
    else:
        pytest.fail(f"Cannot check containment for type {type(container).__name__}")


def assert_dict_contains_subset(superset: Dict[str, Any], subset: Dict[str, Any], path: str = "") -> None:
    """Assert superset dict contains all key-value pairs from subset (recursive)."""
    for key, expected_value in subset.items():
        current_path = f"{path}.{key}" if path else str(key)
        assert_contains(superset, key, path=current_path)
        actual_value = superset[key]
        if isinstance(expected_value, dict) and isinstance(actual_value, dict):
            assert_dict_contains_subset(actual_value, expected_value, path=current_path)
        else:
            assert_equals(actual_value, expected_value, msg=f"Key mismatch at {current_path}")


def assert_records_match(actual_rows: List[Dict[str, Any]], expected_rows: List[Dict[str, Any]]) -> None:
    """Assert two lists of dicts (DB rows) match, ignoring row order.

    Each row is compared as a dict subset.
    """
    assert_equals(len(actual_rows), len(expected_rows), msg=f"Row count mismatch")
    for expected in expected_rows:
        found = any(
            all(actual.get(k) == v for k, v in expected.items())
            for actual in actual_rows
        )
        if not found:
            pytest.fail(f"Row not found in results: {expected}. Actual rows: {actual_rows}")
