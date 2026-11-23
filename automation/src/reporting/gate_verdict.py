"""Evaluate Test Gate using the required pytest artifacts and job conclusions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__:
    from .business_scenarios import SOURCE_LABELS
    from .report_data import SOURCES_FILE, run_metadata
else:
    from business_scenarios import SOURCE_LABELS
    from report_data import SOURCES_FILE, run_metadata


PASSED = "通过"
FAILED = "失败"
UNVERIFIED = "未验收"
VERDICT_CODES = {PASSED: "passed", FAILED: "failed", UNVERIFIED: "unverified"}
ALLURE_OUTCOMES = {
    "passed": "passed", "failed": "failed", "broken": "failed", "skipped": "skipped"
}


@dataclass(frozen=True)
class RequiredSuite:
    key: str
    label: str
    job: str
    allure_artifact: str


REQUIRED_SUITES = (
    RequiredSuite("quality-infra", "框架与基础设施", "quality-gate", "allure-results-quality"),
    RequiredSuite("quality-api", "Mock API", "quality-gate", "allure-results-quality"),
    RequiredSuite("real", "真实后端 Smoke", "real-smoke", "allure-results-real-smoke"),
    RequiredSuite("contract", "OpenAPI 契约", "real-smoke", "allure-results-real-smoke"),
)


@dataclass(frozen=True)
class SuiteResult:
    label: str
    status: str
    reason: str
    executed: int | None = None
    skipped: int | None = None
    failed: int = 0


def _count_junit_cases(root: ET.Element) -> Counter[str]:
    # pytest can emit two records for one node when its test and teardown both fail.
    outcomes = {}
    priority = {"passed": 0, "skipped": 1, "failed": 2}
    for case in root.iter("testcase"):
        key = (case.get("file"), case.get("classname"), case.get("name"))
        if not key[-1]:
            raise ValueError("Missing JUnit case name")
        if case.find("failure") is not None or case.find("error") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        if key not in outcomes or priority[status] > priority[outcomes[key]]:
            outcomes[key] = status
    return Counter(outcomes.values())


def _count_allure_cases(directory: Path) -> Counter[str]:
    outcomes: Counter[str] = Counter()
    seen = set()
    for path in directory.glob("*-result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("Invalid Allure case")
        case_id, name, status = (result.get(field) for field in ("uuid", "name", "status"))
        if not all(isinstance(value, str) and value.strip() for value in (case_id, name, status)):
            raise ValueError("Invalid Allure case")
        if case_id in seen or status not in ALLURE_OUTCOMES:
            raise ValueError("Duplicate or unknown Allure case")
        seen.add(case_id)
        outcomes[ALLURE_OUTCOMES[status]] += 1
    return outcomes


def _describe_counts(counts: Counter[str]) -> str:
    return f"通过 {counts['passed']}、失败或错误 {counts['failed']}、跳过 {counts['skipped']}"


def _evaluate_suite(
    suite: RequiredSuite, conclusion: str, junit_dir: Path, allure_dir: Path
) -> SuiteResult:
    if conclusion not in ("success", "failure"):
        reason = {
            "cancelled": "必跑 job 已取消",
            "skipped": "必跑 job 已跳过",
        }.get(conclusion, "缺少必跑 job 的运行结论")
        return SuiteResult(suite.label, UNVERIFIED, reason)

    junit_path = junit_dir / f"junit-{suite.key}.xml"
    try:
        root = ET.parse(junit_path).getroot()
    except FileNotFoundError:
        return SuiteResult(suite.label, UNVERIFIED, "缺少 JUnit 产物")
    except (OSError, ET.ParseError):
        return SuiteResult(suite.label, UNVERIFIED, "JUnit 产物不可读")
    if root.tag not in ("testsuites", "testsuite"):
        return SuiteResult(suite.label, UNVERIFIED, "JUnit 产物格式无效")

    try:
        junit = _count_junit_cases(root)
    except ValueError:
        return SuiteResult(suite.label, UNVERIFIED, "JUnit 产物格式无效")
    skipped = junit["skipped"]
    failed = junit["failed"]
    executed = junit["passed"] + failed
    if not executed:
        reason = "全部用例已跳过" if skipped else "JUnit 无用例记录"
        return SuiteResult(suite.label, UNVERIFIED, reason, executed, skipped)

    try:
        allure = _count_allure_cases(allure_dir / suite.allure_artifact / suite.key)
    except (OSError, ValueError):
        return SuiteResult(suite.label, UNVERIFIED, "Allure 用例结果损坏或重复", executed, skipped, failed)
    if not allure:
        return SuiteResult(suite.label, UNVERIFIED, "缺少 Allure 用例结果", executed, skipped, failed)
    if allure != junit:
        reason = f"执行记录不一致（JUnit {_describe_counts(junit)} / Allure {_describe_counts(allure)}）"
        return SuiteResult(suite.label, UNVERIFIED, reason, executed, skipped, failed)
    if failed:
        return SuiteResult(suite.label, FAILED, f"{failed} 条失败或错误", executed, skipped, failed)
    return SuiteResult(suite.label, PASSED, "执行和产物检查通过", executed, skipped)


def evaluate_gate(
    junit_dir: Path, allure_dir: Path, job_results: dict[str, str]
) -> tuple[str, list[SuiteResult], list[str]]:
    results = [
        _evaluate_suite(suite, job_results.get(suite.job, ""), junit_dir, allure_dir)
        for suite in REQUIRED_SUITES
    ]
    failed_jobs = [
        job for job in dict.fromkeys(suite.job for suite in REQUIRED_SUITES)
        if job_results.get(job) == "failure"
    ]
    if any(result.status == UNVERIFIED for result in results):
        verdict = UNVERIFIED
    elif any(result.status == FAILED for result in results) or failed_jobs:
        verdict = FAILED
    else:
        verdict = PASSED
    return verdict, results, failed_jobs


def render_summary(verdict: str, results: list[SuiteResult], failed_jobs: list[str]) -> str:
    headline = f"## Test Gate 判定：{verdict}"
    if verdict == UNVERIFIED and any(result.failed for result in results):
        headline += "（同时存在已确认的测试失败）"
    lines = [
        headline,
        "",
        "| 必跑套件 | 判定 | 执行 | 跳过 | 失败或错误 | 原因 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        executed = result.executed if result.executed is not None else "—"
        skipped = result.skipped if result.skipped is not None else "—"
        failed = result.failed if result.executed is not None else "—"
        lines.append(
            f"| {result.label} | {result.status} | {executed} | {skipped} | {failed} | {result.reason} |"
        )
    for job in failed_jobs:
        lines.append(f"\n- 必跑 job `{job}` 执行失败。")
    return "\n".join(lines) + "\n"


def build_summary(
    junit_dir: Path, allure_dir: Path, job_results: dict[str, str]
) -> tuple[str, str]:
    verdict, results, failed_jobs = evaluate_gate(junit_dir, allure_dir, job_results)
    return verdict, render_summary(verdict, results, failed_jobs)


def merge_allure_artifacts(artifacts_dir: Path, destination: Path) -> None:
    """Flatten per-job and per-suite artifacts for Allure and the HTML reports."""
    destination.mkdir(parents=True, exist_ok=True)
    suite_labels = {
        (suite.allure_artifact, suite.key): SOURCE_LABELS[suite.key] for suite in REQUIRED_SUITES
    }
    observer_labels = {
        "allure-results-db": SOURCE_LABELS["db"],
        "allure-results-scenarios": SOURCE_LABELS["scenarios"],
    }
    sources = {}
    for path in sorted(artifacts_dir.glob("allure-results-*/**/*")):
        if not path.is_file() or path.name in ("environment.properties", "executor.json"):
            continue
        shutil.copy2(path, destination / path.name)
        if path.name.endswith("-result.json"):
            parts = path.relative_to(artifacts_dir).parts
            source = (suite_labels.get(parts[:2]) or observer_labels.get(parts[0]))
            if source:
                sources[path.name] = source
    (destination / SOURCES_FILE).write_text(
        json.dumps(sources, ensure_ascii=False), encoding="utf-8"
    )

    run = run_metadata()
    fields = {
        "Git.Branch": run["branch"],
        "Git.Commit": run["commit"],
        "Actions.Run": run["run_id"],
        "Actions.Attempt": run["attempt"],
        "Report.Sources": ", ".join(sorted(set(sources.values()))),
    }
    properties = [
        f"{key}={' '.join(value.splitlines())}" for key, value in fields.items() if value
    ]
    (destination / "environment.properties").write_text(
        "\n".join(properties) + "\n", encoding="utf-8"
    )
    if run["run_url"]:
        (destination / "executor.json").write_text(
            json.dumps({
                "name": "GitHub Actions", "type": "github",
                "url": run["run_url"], "buildUrl": run["run_url"],
            }),
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-dir", type=Path, required=True)
    parser.add_argument("--allure-artifacts-dir", type=Path, required=True)
    parser.add_argument("--quality-job", required=True)
    parser.add_argument("--real-job", required=True)
    parser.add_argument("--report-context", type=Path)
    args = parser.parse_args(argv)

    verdict, results, failed_jobs = evaluate_gate(
        args.junit_dir,
        args.allure_artifacts_dir,
        {"quality-gate": args.quality_job, "real-smoke": args.real_job},
    )
    summary = render_summary(verdict, results, failed_jobs)
    print(summary, end="")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as output:
            output.write(summary + "\n")

    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"verdict={VERDICT_CODES[verdict]}\n")
    if args.report_context:
        args.report_context.parent.mkdir(parents=True, exist_ok=True)
        context = {
            "verdict": VERDICT_CODES[verdict],
            "confirmed_failures": any(result.failed for result in results),
            "suites": [asdict(result) for result in results],
            "run": run_metadata(),
        }
        temporary_context = args.report_context.with_suffix(".tmp")
        temporary_context.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        temporary_context.replace(args.report_context)
    return 0 if verdict == PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
