"""Exercise the gate with artifacts emitted by pytest and Allure."""

import os
import subprocess
import sys

from automation.src.reporting import business_report, gate_verdict


PYTEST_CASES = """
import allure
import pytest


@pytest.mark.parametrize("number", [1, 2])
def test_healthy(number):
    assert number > 0


@pytest.mark.xfail
def test_healthy_expected_failure():
    assert False


def test_all_skipped():
    pytest.skip("unavailable")


@pytest.fixture
def broken_setup():
    raise RuntimeError("fixture setup")


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("fixture teardown")


def test_fixture_setup(broken_setup):
    pass


def test_fixture_failure_and_teardown(broken_teardown):
    assert False


def test_fixture_passing_call_with_teardown_error(broken_teardown):
    pass


@allure.feature("Observed work orders")
@allure.title("Observed work order scenario")
def test_observation():
    pass
"""


def test_gate_with_pytest_artifacts_and_observed_scenario(tmp_path, monkeypatch):
    pytest_file = tmp_path / "test_generated_gate_cases.py"
    pytest_file.write_text(PYTEST_CASES, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    junit_dir = tmp_path / "gate-junit"
    allure_dir = tmp_path / "allure-artifacts"

    def run_pytest(selection, junit, allure, exit_code=0):
        completed = subprocess.run(
            [
                sys.executable, "-B", "-m", "pytest", "-c", str(config), "-p",
                "no:cacheprovider", "-q", str(pytest_file), "-k", selection,
                f"--junitxml={junit}", f"--alluredir={allure}", "--clean-alluredir",
            ],
            cwd=tmp_path,
            env={**os.environ, "PYTEST_ADDOPTS": ""},
            capture_output=True,
            timeout=40,
        )
        assert completed.returncode == exit_code, completed.stdout.decode(errors="replace")

    for suite in gate_verdict.REQUIRED_SUITES:
        run_pytest(
            "healthy", junit_dir / f"junit-{suite.key}.xml",
            allure_dir / suite.allure_artifact / suite.key,
        )
    run_pytest("observation", tmp_path / "observation.xml", allure_dir / "allure-results-scenarios")

    def check(expected, summary_fragment, quality="success", real="success"):
        step_output = tmp_path / "step-output"
        step_summary = tmp_path / "summary.md"
        for path in (step_output, step_summary):
            path.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(step_output))
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
        result = gate_verdict.main([
            "--junit-dir", str(junit_dir), "--allure-artifacts-dir", str(allure_dir),
            "--quality-job", quality, "--real-job", real,
        ])
        assert result == (0 if expected == "passed" else 1)
        assert step_output.read_text(encoding="utf-8").strip() == f"verdict={expected}"
        assert summary_fragment in step_summary.read_text(encoding="utf-8")

    check("passed", "| OpenAPI 契约 | 通过 | 2 | 1 | 0 |")
    check("unverified", "必跑 job 已取消", real="cancelled")

    contract_junit = junit_dir / "junit-contract.xml"
    contract_allure = allure_dir / "allure-results-real-smoke/contract"
    junit_saved = contract_junit.read_bytes()
    contract_junit.unlink()
    check("unverified", "缺少 JUnit 产物")
    contract_junit.write_bytes(junit_saved)

    case_result = next(contract_allure.glob("*-result.json"))
    case_saved = case_result.read_bytes()
    case_result.unlink()
    check("unverified", "执行记录不一致")
    case_result.write_bytes(b"{")
    check("unverified", "Allure 用例结果损坏或重复")
    case_result.write_bytes(case_saved)

    # The observer's case survives flattening and becomes part of both report inputs.
    merged = tmp_path / "allure-results"
    gate_verdict.merge_allure_artifacts(allure_dir, merged)
    assert len(list(merged.glob("*-result.json"))) == 13
    business_html = tmp_path / "business.html"
    business_report.main(["--results", str(merged), "--out", str(business_html)])
    assert "Observed work order scenario" in business_html.read_text(encoding="utf-8")

    run_pytest("all_skipped", contract_junit, contract_allure)
    check("unverified", "全部用例已跳过")

    run_pytest(
        "fixture", junit_dir / "junit-quality-infra.xml",
        allure_dir / "allure-results-quality/quality-infra", exit_code=1,
    )
    check("unverified", "同时存在已确认的测试失败", quality="failure")
    run_pytest("healthy", contract_junit, contract_allure)
    check("failed", "| 框架与基础设施 | 失败 | 3 | 0 | 3 |", quality="failure")
