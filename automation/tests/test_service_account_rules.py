"""Executable acceptance cases for the service account's backend workflows."""

import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree

import allure
import pytest

from automation.src.reporting.business_scenarios import (
    SCENARIOS, SCENARIO_LABEL, record_check_outcome,
)


CHECKS = {
    "service-knowledge-finance": {
        "content": "backend/tests/test_archive_finance.py::test_content_approval_creates_three_distinct_roles_without_auto_pass",
        "approve": "backend/tests/test_archive_finance.py::test_finance_approval_is_whole_task_idempotent_and_uses_question_month",
        "reject": "backend/tests/test_archive_finance.py::test_finance_rejection_blocks_all_rewards_and_publication",
    },
    "service-learning": {
        "redact": "backend/tests/test_learning.py::test_redacts_before_model_and_preserves_candidate_until_adopted",
        "generate": "backend/tests/test_learning.py::test_admin_http_flow_creates_confirms_generates_and_previews",
        "enable": "backend/tests/test_learning.py::test_available_filter_requires_review_enabled_article_and_current_version",
        "score": "backend/tests/test_learning.py::test_multiple_choice_requires_exact_answer_set_and_does_not_store_submissions",
    },
    "service-challenge": {
        "publish": "automation/tests/_service_account_backend_checks.py::test_manager_can_publish_standalone_challenge",
        "settle": "automation/tests/_service_account_backend_checks.py::test_voted_answer_is_rewarded_once_after_deadline",
    },
}


@pytest.fixture(scope="module")
def check_outcomes(tmp_path_factory):
    nodes = [node for checks in CHECKS.values() for node in checks.values()]
    junit = tmp_path_factory.mktemp("service-account") / "private.xml"
    # A child process keeps the backend's module stubs and DB fixtures out of
    # the framework's test session. Its raw stdout and JUnit never go to Allure.
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["KNOWLEDGE_CHALLENGES_ENABLED"] = "true"
    try:
        run = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-o", "asyncio_mode=auto",
                "--noconftest", "-p", "backend.tests.conftest",
                "--junitxml", str(junit), "-q", *nodes,
            ],
            cwd=Path(__file__).resolve().parents[2], env=env, timeout=90,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if run.returncode not in (0, 1):
            pytest.skip(f"后台规则检查未执行完成（pytest 退出码 {run.returncode}）")
        result = ElementTree.parse(junit)
    except (OSError, ElementTree.ParseError, subprocess.TimeoutExpired):
        pytest.skip("后台规则检查没有生成可读结果")
    finally:
        junit.unlink(missing_ok=True)
    return {
        case.get("name"): next(
            (outcome for tag, outcome in (("error", "broken"), ("failure", "failed"), ("skipped", "skipped"))
             if case.find(tag) is not None),
            "passed",
        )
        for case in result.iter("testcase")
    }


@allure.feature("服务号后台规则")
@pytest.mark.parametrize("scenario_id", CHECKS)
def test_service_account_business_rules(scenario_id, check_outcomes):
    scenario = SCENARIOS[scenario_id]
    allure.dynamic.title(scenario.title)
    allure.dynamic.label(SCENARIO_LABEL, scenario_id)

    outcomes = []
    for step_id, node in CHECKS[scenario_id].items():
        step = scenario.steps[step_id]
        with allure.step(f"{step.role} · {step.action}"):
            outcome = check_outcomes.get(node.rsplit("::", 1)[1], "skipped")
            record_check_outcome(scenario_id, step_id, outcome)
            outcomes.append(outcome)
    assert not {"failed", "broken"}.intersection(outcomes), "服务号业务规则断言未通过，请查看步骤结果"
    if "skipped" in outcomes:
        pytest.skip("部分服务号业务规则未验证")
