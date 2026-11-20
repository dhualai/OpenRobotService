"""Shared fixtures for AI evaluation tests."""

import os
from pathlib import Path

import httpx
import pytest

from automation.config.paths import OUTPUT_DIR
from automation.src.ai_metrics import dump_if_needed, get_recorder

RUNS_ROOT = OUTPUT_DIR / "ai-eval-runs"


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


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Dump the run record (run.json + results.csv) after the AI eval session.

    Only writes when AI eval cases actually recorded results; honors
    AI_EVAL_NO_RUN=1 to disable. Failures here never change the exit code.
    """
    from automation.tests.ai.runner import loaded_suite_counts

    get_recorder().set_golden_counts(loaded_suite_counts())
    run_dir = dump_if_needed(RUNS_ROOT)
    if run_dir is not None:
        print(f"\n[ai-eval] run recorded: {run_dir}")
