"""OpenAPI contract smoke tests powered by Schemathesis."""

from __future__ import annotations

import json
import os

import allure
import pytest

from automation.config import load_config
from automation.src.contract import run_contract, schemathesis_available

pytestmark = [pytest.mark.contract, pytest.mark.api]


def _enabled() -> bool:
    return os.getenv("CONTRACT_TEST_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def test_openapi_contract_smoke(tmp_path):
    """Run a bounded, positive Schemathesis smoke against safe paths."""
    if not _enabled():
        pytest.skip("CONTRACT_TEST_ENABLED=1 is required for contract tests")
    if not schemathesis_available():
        pytest.skip("schemathesis is not installed")

    config = load_config(env=os.getenv("AUTOMATION_ENV", "local"))
    base_url = os.getenv("OPENAPI_BASE_URL", config.api.base_url)
    schema = os.getenv("OPENAPI_SCHEMA_URL", f"{base_url.rstrip('/')}/api/openapi.json")
    include_path_regex = os.getenv("CONTRACT_INCLUDE_PATH_REGEX", "^/api/(health|auth/login)$")
    phases = os.getenv("CONTRACT_PHASES", "examples,coverage")
    mode = os.getenv("CONTRACT_MODE", "positive")
    max_examples = int(os.getenv("CONTRACT_MAX_EXAMPLES", "5"))

    result = run_contract(
        schema=schema,
        base_url=base_url,
        cwd=tmp_path,
        phases=phases,
        mode=mode,
        max_examples=max_examples,
        include_path_regex=include_path_regex,
        report_dir=tmp_path / "schemathesis-report",
        junit_path=tmp_path / "schemathesis-junit.xml",
        timeout=float(os.getenv("CONTRACT_TIMEOUT", "300")),
    )

    allure.attach(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        name="schemathesis-result",
        attachment_type=allure.attachment_type.JSON,
    )
    assert result["passed"], (
        f"Schemathesis contract failed with code {result['returncode']}\n"
        f"STDOUT:\n{result['stdout']}\nSTDERR:\n{result['stderr']}"
    )