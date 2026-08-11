"""Shared fixtures for AI evaluation tests."""

import os

import httpx
import pytest


@pytest.fixture(scope="session")
async def ai_client():
    """httpx client pointed at the real AI service (default localhost:8401).

    Skips the test when the service is unreachable, so local runs without
    the AI service do not report false failures. Session-scoped so the
    connectivity probe runs once per test session.
    """
    base_url = os.getenv("AI_EVAL_BASE_URL", "http://localhost:8401")
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        try:
            r = await client.get("/api/ai/qa/health")
        except httpx.HTTPError:
            pytest.skip(f"AI service unreachable at {base_url}")
        if r.status_code != 200:
            pytest.skip(f"AI service health check failed: HTTP {r.status_code}")
        yield client
