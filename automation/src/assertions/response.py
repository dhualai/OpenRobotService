import json

import pytest
from typing import Any, Dict, Optional

from automation.src.assertions.report import record


def assert_status_code(response: Any, expected: int, msg: str = "") -> None:
    """Assert HTTP response status code matches expected.

    Args:
        response: HTTP response object (httpx.Response or similar).
        expected: Expected status code.
        msg: Optional custom error message.
    """
    actual = getattr(response, "status_code", None)
    if actual is None:
        pytest.fail(f"Response object has no status_code attribute: {type(response)}")
    try:
        record({"断言": f"状态码 == {expected}", "期望值": expected, "实际值": actual})
    except Exception:
        pass
    if actual != expected:
        detail = msg or f"Expected status {expected}, got {actual}"
        body = getattr(response, "text", "") or str(getattr(response, "content", b""))
        pytest.fail(f"{detail}. Response body: {body[:500]}")


def assert_json_response(response: Any) -> Dict[str, Any]:
    """Assert response is valid JSON and return parsed body.

    Args:
        response: HTTP response object.

    Returns:
        Parsed JSON dict.

    Raises:
        pytest.fail: If response is not valid JSON.
    """
    assert_status_code(response, 200)
    try:
        data = response.json()
        return data
    except (ValueError, AttributeError) as e:
        body = getattr(response, "text", "") or str(getattr(response, "content", b""))
        pytest.fail(f"Response is not valid JSON: {e}. Body: {body[:500]}")


def assert_error_response(response: Any, status_code: int, error_code: Optional[str] = None) -> None:
    """Assert error response with expected status and optional error code.

    Args:
        response: HTTP response object.
        status_code: Expected HTTP status code.
        error_code: Expected error code string in JSON body.
    """
    assert_status_code(response, status_code, msg=f"Expected error status {status_code}")
    try:
        data = response.json()
    except (ValueError, AttributeError):
        return
    if error_code and isinstance(data, dict):
        actual_code = data.get("error_code") or data.get("code") or ""
        if actual_code != error_code:
            pytest.fail(f"Expected error_code={error_code}, got {actual_code}. Body: {data}")
