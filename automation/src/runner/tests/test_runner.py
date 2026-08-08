"""Tests for the Excel-driven runner (load_cases / run_case)."""

import httpx
import pytest
from _pytest.outcomes import Failed

from automation.src.mocks.backend_mock import create_mock_transport
from automation.src.runner import load_cases, run_case


def test_load_cases_returns_cases_for_existing_module():
    cases = load_cases("call")
    assert isinstance(cases, list)
    assert len(cases) > 0
    for case in cases:
        assert case["id"]
        if case.get("steps"):
            continue
        assert case["method"]
        assert case["path"]
        assert isinstance(case["expected_status"], int)


def test_load_cases_unknown_module_returns_empty():
    assert load_cases("no_such_module") == []


def test_load_cases_parses_payload_json():
    cases = load_cases("call")
    sample = next(c for c in cases if c["payload"])
    assert isinstance(sample["payload"], dict)


async def test_run_case_ok():
    transport = create_mock_transport()
    case = {
        "id": "SELF-001",
        "method": "GET",
        "path": "/health",
        "auth": "N",
        "role": "admin",
        "payload": {},
        "expected_status": 200,
        "expected_fields": None,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await run_case(client, {}, case)


async def test_run_case_with_auth_role():
    transport = create_mock_transport()
    case = {
        "id": "SELF-002",
        "method": "GET",
        "path": "/api/auth/me",
        "auth": "Y",
        "role": "engineer",
        "payload": {},
        "expected_status": 200,
        "expected_fields": None,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await run_case(client, {}, case)


async def test_run_case_asserts_status():
    transport = create_mock_transport()
    case = {
        "id": "SELF-003",
        "method": "GET",
        "path": "/health",
        "auth": "N",
        "role": "admin",
        "payload": {},
        "expected_status": 404,
        "expected_fields": None,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(Failed):
            await run_case(client, {}, case)
