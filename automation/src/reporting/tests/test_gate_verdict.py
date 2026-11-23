import json
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

from automation.src.reporting.gate_verdict import (
    FAILED,
    PASSED,
    REQUIRED_SUITES,
    UNVERIFIED,
    build_summary,
    main,
)


@pytest.fixture
def gate_artifacts(tmp_path):
    junit_dir = tmp_path / "junit"
    junit_dir.mkdir()
    allure_dir = tmp_path / "artifacts"
    for suite in REQUIRED_SUITES:
        (junit_dir / f"junit-{suite.key}.xml").write_text(
            '<testsuites><testsuite><testcase name="run"/>'
            '<testcase name="optional"><skipped/></testcase></testsuite></testsuites>',
            encoding="utf-8",
        )
        suite_dir = allure_dir / suite.allure_artifact / suite.key
        suite_dir.mkdir(parents=True)
        (suite_dir / f"{suite.key}-result.json").write_text(
            json.dumps({"name": suite.label, "status": "passed"}), encoding="utf-8"
        )
    return junit_dir, allure_dir


def test_required_suites_allow_partial_skips_and_ignore_observation_jobs(gate_artifacts):
    status, summary = build_summary(
        *gate_artifacts,
        {"quality-gate": "success", "real-smoke": "success", "scenarios": "failure"},
    )
    assert status == PASSED
    assert summary.count("| 通过 | 1 | 1 |") == len(REQUIRED_SUITES)


@pytest.mark.parametrize(
    ("junit", "allure", "conclusion", "reason"),
    [
        ("<testsuite><testcase><skipped/></testcase></testsuite>", "passed", "success",
         "全部用例已跳过"),
        ("<testsuite/>", "passed", "success", "JUnit 无用例记录"),
        ("ok", "passed", "cancelled", "必跑 job 已取消"),
        ("ok", "passed", "skipped", "必跑 job 已跳过"),
        ("ok", "passed", "", "缺少必跑 job 的运行结论"),
        (None, "passed", "success", "缺少 JUnit 产物"),
        ("<testsuite>", "passed", "success", "JUnit 产物不可读"),
        ("<notes/>", "passed", "success", "JUnit 产物格式无效"),
        ("ok", None, "success", "缺少有效的 Allure 执行结果"),
        ("ok", "skipped", "success", "缺少有效的 Allure 执行结果"),
        ("ok", "invalid-json", "success", "缺少有效的 Allure 执行结果"),
    ],
)
def test_incomplete_required_suite_is_unverified(
    gate_artifacts, junit, allure, conclusion, reason
):
    junit_dir, allure_dir = gate_artifacts
    contract_junit = junit_dir / "junit-contract.xml"
    contract_allure = allure_dir / "allure-results-real-smoke/contract/contract-result.json"
    if junit is None:
        contract_junit.unlink()
    elif junit != "ok":
        contract_junit.write_text(junit, encoding="utf-8")
    if allure is None:
        contract_allure.unlink()
    elif allure == "invalid-json":
        contract_allure.write_text("{", encoding="utf-8")
    else:
        contract_allure.write_text(json.dumps({"status": allure}), encoding="utf-8")

    status, summary = build_summary(
        junit_dir, allure_dir, {"quality-gate": "success", "real-smoke": conclusion}
    )
    assert status == UNVERIFIED
    assert "## Test Gate 判定：未验收" in summary
    contract_line = next(line for line in summary.splitlines() if line.startswith("| OpenAPI 契约"))
    assert "| 未验收 |" in contract_line
    assert reason in contract_line
    assert "| Mock API | 通过 |" in summary


@pytest.mark.parametrize("result_tag", ["failure", "error"])
def test_junit_test_failure_is_preserved(gate_artifacts, result_tag):
    junit_dir, allure_dir = gate_artifacts
    (junit_dir / "junit-quality-api.xml").write_text(
        f"<testsuite><testcase><{result_tag}/></testcase></testsuite>", encoding="utf-8"
    )
    status, summary = build_summary(
        junit_dir, allure_dir, {"quality-gate": "failure", "real-smoke": "success"}
    )
    assert status == FAILED
    assert "| Mock API | 失败 | 1 | 0 | 1 条失败或错误 |" in summary


def test_failed_job_with_passing_test_results_still_fails_gate(gate_artifacts):
    status, summary = build_summary(
        *gate_artifacts, {"quality-gate": "failure", "real-smoke": "success"}
    )
    assert status == FAILED
    assert "必跑 job `quality-gate` 执行失败" in summary


@pytest.mark.parametrize(("conclusion", "exit_code"), [("success", 0), ("cancelled", 1)])
def test_cli_writes_gate_summary_and_keeps_available_reports(
    gate_artifacts, monkeypatch, tmp_path, conclusion, exit_code
):
    junit_dir, allure_dir = gate_artifacts
    scenarios = allure_dir / "allure-results-scenarios"
    scenarios.mkdir()
    (scenarios / "scenario-result.json").write_text('{"status":"passed"}', encoding="utf-8")
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    merged = tmp_path / "merged"

    assert main([
        "--junit-dir", str(junit_dir), "--allure-artifacts-dir", str(allure_dir),
        "--merged-allure-dir", str(merged), "--quality-job", "success", "--real-job", conclusion,
    ]) == exit_code

    expected = PASSED if exit_code == 0 else UNVERIFIED
    assert f"## Test Gate 判定：{expected}" in summary_path.read_text(encoding="utf-8")
    assert len(list(merged.glob("*-result.json"))) == len(REQUIRED_SUITES) + 1
    assert (merged / "scenario-result.json").is_file()


def test_required_suite_artifacts_are_connected_to_enforced_verdict():
    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/test-gate.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    report = jobs["allure-report"]
    download = {
        step["with"]["pattern"]: step["with"]
        for step in report["steps"] if step.get("uses", "").startswith("actions/download-artifact@")
    }
    assert download["allure-results-*"]["merge-multiple"] is False
    assert download["gate-junit-*"]["merge-multiple"] is True
    for suite in REQUIRED_SUITES:
        steps = jobs[suite.job]["steps"]
        assert suite.job in report["needs"]
        assert any(
            f"--alluredir=automation/output/allure-results/{suite.key} " in step.get("run", "")
            for step in steps
        )
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        assert any(step["with"]["name"] == suite.allure_artifact and step["if"] == "always()"
                   for step in uploads)
        assert any(fnmatchcase(step["with"]["name"], "gate-junit-*")
                   and f"automation/output/junit-{suite.key}.xml" in step["with"]["path"].splitlines()
                   and step["if"] == "always()" for step in uploads)

    evaluate = next(step for step in report["steps"] if step.get("id") == "gate-verdict")
    assert evaluate["if"] == "always()"
    assert evaluate["continue-on-error"] is True
    enforce = report["steps"][-1]
    assert enforce["if"] == "always()"
    assert enforce["env"]["VERDICT_OUTCOME"] == "${{ steps.gate-verdict.outcome }}"
    assert "exit 1" in enforce["run"]
