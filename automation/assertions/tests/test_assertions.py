"""Tests for the assertions module."""

from unittest.mock import MagicMock

import pytest

from automation.assertions import (
    assert_contains, assert_dict_contains_subset, assert_equals, assert_not_empty, assert_records_match, assert_status_code
)
from automation.assertions.response import assert_error_response, assert_json_response
from automation.assertions.timing import assert_max_duration, assert_min_duration, assert_duration_between


class TestAssertEquals:
    def test_equal(self):
        assert_equals(1, 1)
        assert_equals("hello", "hello")
        assert_equals(None, None)

    def test_not_equal(self):
        with pytest.raises(pytest.fail.Exception):
            assert_equals(1, 2)

    def test_custom_message(self):
        with pytest.raises(pytest.fail.Exception, match="custom msg"):
            assert_equals(1, 2, msg="custom msg")


class TestAssertNotEmpty:
    def test_non_empty(self):
        assert_not_empty([1])
        assert_not_empty("a")
        assert_not_empty({"key": "val"})

    def test_empty_list(self):
        with pytest.raises(pytest.fail.Exception, match="non-empty"):
            assert_not_empty([])

    def test_empty_string(self):
        with pytest.raises(pytest.fail.Exception):
            assert_not_empty("")


class TestAssertContains:
    def test_contains_list(self):
        assert_contains([1, 2, 3], 2)

    def test_contains_dict(self):
        assert_contains({"a": 1, "b": 2}, "a")

    def test_not_contains(self):
        with pytest.raises(pytest.fail.Exception):
            assert_contains([1, 2], 3)


class TestAssertDictContainsSubset:
    def test_subset_match(self):
        assert_dict_contains_subset({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 2})

    def test_nested_subset(self):
        assert_dict_contains_subset(
            {"user": {"name": "alice", "age": 30}, "role": "admin"},
            {"user": {"name": "alice"}},
        )

    def test_subset_mismatch(self):
        with pytest.raises(pytest.fail.Exception):
            assert_dict_contains_subset({"a": 1}, {"a": 2})


class TestAssertRecordsMatch:
    def test_records_match(self):
        actual = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        expected = [{"id": 2, "name": "b"}, {"id": 1, "name": "a"}]
        assert_records_match(actual, expected)

    def test_records_count_mismatch(self):
        with pytest.raises(pytest.fail.Exception, match="Row count mismatch"):
            assert_records_match([{"id": 1}], [{"id": 1}, {"id": 2}])


class TestAssertStatusCode:
    def test_status_match(self):
        resp = MagicMock(status_code=200)
        assert_status_code(resp, 200)

    def test_status_mismatch(self):
        resp = MagicMock(status_code=404, text="Not Found")
        with pytest.raises(pytest.fail.Exception, match="Expected status 200"):
            assert_status_code(resp, 200)


class TestAssertJsonResponse:
    def test_valid_json(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"key": "val"}
        result = assert_json_response(resp)
        assert result == {"key": "val"}

    def test_non_json(self):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("not json")
        resp.text = "not json"
        with pytest.raises(pytest.fail.Exception):
            assert_json_response(resp)


class TestAssertErrorResponse:
    def test_error_match(self):
        resp = MagicMock(status_code=400)
        resp.json.return_value = {"error_code": "BAD_REQUEST"}
        assert_error_response(resp, 400, error_code="BAD_REQUEST")

    def test_error_status_only(self):
        resp = MagicMock(status_code=403, text="Forbidden")
        assert_error_response(resp, 403)


class TestTimingAssertions:
    def test_max_duration_pass(self):
        result = assert_max_duration(lambda: 42, max_ms=1000)
        assert result == 42

    def test_min_duration_pass(self):
        import time

        def slow_op():
            time.sleep(0.01)
            return "done"

        result = assert_min_duration(slow_op, min_ms=1)
        assert result == "done"

    def test_duration_between_pass(self):
        result = assert_duration_between(lambda: "ok", min_ms=0, max_ms=5000)
        assert result == "ok"
