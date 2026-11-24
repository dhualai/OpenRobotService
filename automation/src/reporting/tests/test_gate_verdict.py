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
    merge_allure_artifacts,
)


def write_allure_case(path, status):
    path.write_text(
        json.dumps({"uuid": path.stem, "name": path.stem, "status": status}),
        encoding="utf-8",
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
        write_allure_case(suite_dir / f"{suite.key}-result.json", "passed")
        write_allure_case(suite_dir / f"{suite.key}-skip-result.json", "skipped")
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
        ('<testsuite><testcase name="case"><skipped/></testcase></testsuite>', "passed", "success",
         "全部用例已跳过"),
        ("<testsuite/>", "passed", "success", "JUnit 无用例记录"),
        ("ok", "passed", "cancelled", "必跑 job 已取消"),
        ("ok", "passed", "skipped", "必跑 job 已跳过"),
        ("ok", "passed", "", "缺少必跑 job 的运行结论"),
        (None, "passed", "success", "缺少 JUnit 产物"),
        ("<testsuite>", "passed", "success", "JUnit 产物不可读"),
        ("<notes/>", "passed", "success", "JUnit 产物格式无效"),
        ("ok", None, "success", "执行记录不一致"),
        ("ok", "missing", "success", "缺少 Allure 用例结果"),
        ("ok", "skipped", "success", "执行记录不一致"),
        ("ok", "invalid-json", "success", "Allure 用例结果损坏或重复"),
        ("ok", "duplicate", "success", "Allure 用例结果损坏或重复"),
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
    if allure is None or allure == "missing":
        contract_allure.unlink()
        if allure == "missing":
            (contract_allure.parent / "contract-skip-result.json").unlink()
    elif allure == "invalid-json":
        contract_allure.write_text("{", encoding="utf-8")
    elif allure == "duplicate":
        (contract_allure.parent / "duplicate-result.json").write_bytes(contract_allure.read_bytes())
    else:
        write_allure_case(contract_allure, allure)

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
        f'<testsuite><testcase name="case"><{result_tag}/></testcase></testsuite>', encoding="utf-8"
    )
    suite = allure_dir / "allure-results-quality/quality-api"
    (suite / "quality-api-skip-result.json").unlink()
    write_allure_case(suite / "quality-api-result.json", "failed")
    status, summary = build_summary(
        junit_dir, allure_dir, {"quality-gate": "failure", "real-smoke": "success"}
    )
    assert status == FAILED
    assert "| Mock API | 失败 | 1 | 0 | 1 | 1 条失败或错误 |" in summary


def test_known_failure_is_visible_when_other_evidence_is_missing(gate_artifacts):
    junit_dir, allure_dir = gate_artifacts
    (junit_dir / "junit-quality-api.xml").write_text(
        '<testsuite><testcase name="run"><failure/></testcase>'
        '<testcase name="optional"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    write_allure_case(allure_dir / "allure-results-quality/quality-api/quality-api-result.json",
                      "failed")
    (junit_dir / "junit-contract.xml").unlink()
    status, summary = build_summary(
        junit_dir, allure_dir, {"quality-gate": "failure", "real-smoke": "success"}
    )
    assert status == UNVERIFIED
    assert "未验收（同时存在已确认的测试失败）" in summary
    assert "| Mock API | 失败 |" in summary
    assert "| OpenAPI 契约 | 未验收 |" in summary


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
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    merged = tmp_path / "merged"

    assert main([
        "--junit-dir", str(junit_dir), "--allure-artifacts-dir", str(allure_dir),
        "--quality-job", "success", "--real-job", conclusion,
    ]) == exit_code

    expected = PASSED if exit_code == 0 else UNVERIFIED
    assert f"## Test Gate 判定：{expected}" in summary_path.read_text(encoding="utf-8")
    assert output_path.read_text(encoding="utf-8").strip() == (
        "verdict=passed" if exit_code == 0 else "verdict=unverified"
    )
    assert not merged.exists()
    merge_allure_artifacts(allure_dir, merged)
    assert len(list(merged.glob("*-result.json"))) == 2 * len(REQUIRED_SUITES) + 1
    assert (merged / "scenario-result.json").is_file()


def test_required_suite_artifacts_are_connected_to_enforced_verdict():
    workflow = Path(__file__).resolve().parents[4] / ".github/workflows/test-gate.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    report = jobs["allure-report"]
    download = {
        step["with"]["pattern"]: step["with"]
        for step in report["steps"] if step.get("uses", "").startswith("actions/download-artifact@")
        and "pattern" in step["with"]
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
                   and f"automation/output/safe-junit/junit-{suite.key}.xml"
                   in step["with"]["path"].splitlines()
                   and step["if"] == "always()" for step in uploads)
        assert any(
            "automation.src.reporting.gate_junit" in step.get("run", "")
            and suite.key in step["run"].split()
            and step["if"] == "always()"
            for step in steps
        )

    evaluate = next(step for step in report["steps"] if step.get("id") == "gate-verdict")
    assert evaluate["if"] == "always()"
    assert evaluate["continue-on-error"] is True
    assert "--report-context automation/output/test-gate-report.json" in evaluate["run"]
    for step_id in ("standalone-html", "business-html"):
        renderer = next(step for step in report["steps"] if step.get("id") == step_id)
        assert "--context automation/output/test-gate-report.json --expect-gate" in renderer["run"]
    enforce = report["steps"][-1]
    assert enforce["if"] == "always()"
    assert enforce["env"]["VERDICT_OUTCOME"] == "${{ steps.gate-verdict.outcome }}"
    assert enforce["env"]["TEST_GATE_VERDICT"] == "${{ steps.gate-verdict.outputs.verdict }}"
    assert "未生成有效结论" in enforce["run"]
    for step_name in ("prepare-reports", "allure-html", "business-html", "publish-pages"):
        step = next(step for step in report["steps"] if step.get("id") == step_name)
        assert step["continue-on-error"] is True
    assert "exit 1" in enforce["run"]


def test_business_report_can_load_evidence_from_a_selected_ui_run():
    workflows = Path(__file__).resolve().parents[4] / ".github/workflows"
    gate = yaml.safe_load((workflows / "test-gate.yml").read_text(encoding="utf-8"))
    ui = yaml.safe_load((workflows / "ui-regression.yml").read_text(encoding="utf-8"))
    report = gate["jobs"]["allure-report"]
    [download] = [
        step for step in report["steps"]
        if step.get("with", {}).get("name") == "service-account-ui-evidence"
    ]
    assert report["permissions"]["actions"] == "read"
    assert download["with"]["run-id"] == "${{ inputs.ui_evidence_run_id }}"
    assert download["continue-on-error"] is True
    business = next(step for step in report["steps"] if step.get("id") == "business-html")
    assert "--service-account-ui-environment service-account-test" in business["run"]
    ui_steps = ui["jobs"]["ui-regression"]["steps"]
    assert any("test_service_account_ticket_lifecycle.py" in step.get("run", "") for step in ui_steps)
    [upload] = [
        step for step in ui_steps if step.get("with", {}).get("name") == "service-account-ui-evidence"
    ]
    assert "inputs.deployment_commit == github.sha" in upload["if"]
    assert any(path.endswith("/service-account-ui-run.json") for path in upload["with"]["path"].splitlines())
    verify = next(step for step in ui_steps if step.get("id") == "verify-deployment")
    assert 'git diff --quiet "$UI_DEPLOYMENT_COMMIT" HEAD -- frontend backend ai deploy doc-service' in verify["run"]
