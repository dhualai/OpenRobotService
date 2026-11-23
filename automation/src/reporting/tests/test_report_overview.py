import json
import re

import pytest

from automation.src.reporting import business_report, gate_verdict, report_data, standalone_report


def write_result(directory, name, status, feature="模块"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}-result.json").write_text(
        json.dumps({
            "name": name, "status": status,
            "labels": [{"name": "feature", "value": feature}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("passed", "通过"),
        ("failed", "失败"),
        ("unverified", "未验收（同时存在已确认的测试失败）"),
    ],
)
def test_html_reports_share_the_gate_verdict_and_classified_counts(tmp_path, capsys, verdict, expected):
    results = tmp_path / "results"
    write_result(results, "passed", "passed")
    write_result(results, "failed", "failed")
    write_result(results, "broken", "broken")
    write_result(results, "skipped", "skipped", feature="全跳过模块")
    write_result(results, "unknown", "queued")
    (results / "damaged-result.json").write_text("{", encoding="utf-8")
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({
            "verdict": verdict, "confirmed_failures": True,
            "suites": [{"label": "Mock API", "status": "通过", "executed": 2, "skipped": 1,
                        "failed": 0, "reason": "执行和产物检查通过"}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    reports = []
    for renderer in (business_report, standalone_report):
        output = tmp_path / f"{renderer.__name__.rsplit('.', 1)[-1]}.html"
        renderer.main([
            "--results", str(results), "--out", str(output), "--context", str(context), "--expect-gate",
        ])
        reports.append(output.read_text(encoding="utf-8"))
    for html in reports:
        assert f"Test Gate 判定：{expected}</h2>" in html
        assert "结果总数 6 · 通过 1 · 失败 1 · 中断 1 · 跳过 1 · 未知 2" in html
        assert "<td>Mock API</td><td>通过</td>" in html
    assert re.search(r'全跳过模块<small>.*?</small></td><td>1</td><td>跳过 1</td>', reports[0])

    capsys.readouterr()
    standalone_report.main(["--results", str(results), "--summary"])
    summary = capsys.readouterr().out
    assert "- 结果总数 6\n- 通过 1\n- 失败 1\n- 中断 1\n- 跳过 1\n- 未知 2" in summary


@pytest.mark.parametrize("expect_gate", [False, True])
def test_empty_reports_distinguish_local_use_from_missing_ci_gate(tmp_path, expect_gate):
    reports = []
    for index, renderer in enumerate((business_report, standalone_report)):
        output = tmp_path / f"report-{index}.html"
        args = [
            "--results", str(tmp_path / "missing-results"), "--out", str(output),
            "--context", str(tmp_path / "missing-gate.json"),
        ]
        if expect_gate:
            args.append("--expect-gate")
        renderer.main(args)
        reports.append(output.read_text(encoding="utf-8"))
    for html in reports:
        assert "结果总数 0 · 无可用结果" in html
        assert ("Test Gate 判定：未验收" if expect_gate else "未获取门禁结论") in html
        assert "可以发布" not in html


def test_merged_metadata_uses_the_run_and_preserves_suite_sources(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    mock = artifacts / "allure-results-quality/quality-api"
    real = artifacts / "allure-results-real-smoke/real"
    write_result(mock, "mock-case", "passed")
    write_result(real, "real-case", "skipped")
    for directory, mode in ((mock, "mock"), (real, "real")):
        (directory / "environment.properties").write_text(f"Backend.Mode={mode}\n", encoding="utf-8")
        (directory / "executor.json").write_text(json.dumps({"name": mode}), encoding="utf-8")

    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/OpenRobotService")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("GITHUB_REF_NAME", "test")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    merged = tmp_path / "merged"
    gate_verdict.merge_allure_artifacts(artifacts, merged)
    environment = (merged / "environment.properties").read_text(encoding="utf-8")
    assert "Git.Branch=test" in environment
    assert f"Git.Commit={'f' * 40}" in environment
    assert "Actions.Attempt=2" in environment
    assert "Mock API" in environment and "真实后端 Smoke" in environment
    assert "Backend.Mode=" not in environment
    assert report_data.read_object(merged / "executor.json")["url"].endswith("/actions/runs/123")
    assert {case["name"]: case["source"] for case in report_data.load_results(merged)} == {
        "mock-case": "Mock API", "real-case": "真实后端 Smoke",
    }
