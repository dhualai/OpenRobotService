"""Evaluate Test Gate using the required pytest artifacts and job conclusions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


PASSED = "通过"
FAILED = "失败"
UNVERIFIED = "未验收"


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


def _has_allure_results(directory: Path) -> bool:
    for path in directory.glob("*-result.json"):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(result, dict) and result.get("status") in ("passed", "failed", "broken"):
            return True
    return False


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

    cases = list(root.iter("testcase"))
    skipped = sum(case.find("skipped") is not None for case in cases)
    executed = len(cases) - skipped
    if not executed:
        reason = "全部用例已跳过" if skipped else "JUnit 无用例记录"
        return SuiteResult(suite.label, UNVERIFIED, reason, executed, skipped)

    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    allure_missing = not _has_allure_results(allure_dir / suite.allure_artifact / suite.key)
    if allure_missing:
        reason = "缺少有效的 Allure 执行结果"
        if failed:
            reason += f"，JUnit 记录 {failed} 条失败或错误"
        return SuiteResult(suite.label, UNVERIFIED, reason, executed, skipped)
    if failed:
        return SuiteResult(suite.label, FAILED, f"{failed} 条失败或错误", executed, skipped)
    return SuiteResult(suite.label, PASSED, "执行和产物检查通过", executed, skipped)


def build_summary(
    junit_dir: Path, allure_dir: Path, job_results: dict[str, str]
) -> tuple[str, str]:
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

    lines = [
        f"## Test Gate 判定：{verdict}",
        "",
        "| 必跑套件 | 判定 | 执行 | 跳过 | 原因 |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for result in results:
        executed = result.executed if result.executed is not None else "—"
        skipped = result.skipped if result.skipped is not None else "—"
        lines.append(
            f"| {result.label} | {result.status} | {executed} | {skipped} | {result.reason} |"
        )
    for job in failed_jobs:
        lines.append(f"\n- 必跑 job `{job}` 执行失败。")
    return verdict, "\n".join(lines) + "\n"


def merge_allure_artifacts(artifacts_dir: Path, destination: Path) -> None:
    """Flatten per-job and per-suite artifacts for Allure and the HTML reports."""
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(artifacts_dir.glob("allure-results-*/**/*")):
        if path.is_file():
            shutil.copy2(path, destination / path.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-dir", type=Path, required=True)
    parser.add_argument("--allure-artifacts-dir", type=Path, required=True)
    parser.add_argument("--merged-allure-dir", type=Path, required=True)
    parser.add_argument("--quality-job", required=True)
    parser.add_argument("--real-job", required=True)
    args = parser.parse_args(argv)

    verdict, summary = build_summary(
        args.junit_dir,
        args.allure_artifacts_dir,
        {"quality-gate": args.quality_job, "real-smoke": args.real_job},
    )
    print(summary, end="")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as output:
            output.write(summary + "\n")

    merge_allure_artifacts(args.allure_artifacts_dir, args.merged_allure_dir)
    return 0 if verdict == PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
