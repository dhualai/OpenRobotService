"""Shared inputs and overview for Test Gate reports."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import urlsplit


STATUS_CN = {
    "passed": "通过", "failed": "失败", "broken": "中断", "skipped": "跳过", "unknown": "未知",
}
GATE_CN = {"passed": "通过", "failed": "失败", "unverified": "未验收"}
SOURCES_FILE = "test-gate-sources.json"


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def load_results(root: Path) -> list[dict]:
    sources = read_object(root / SOURCES_FILE)
    results = []
    for path in sorted(root.rglob("*-result.json")):
        data = read_object(path)
        status = data.get("status")
        data["status"] = status if isinstance(status, str) and status in STATUS_CN else "unknown"
        data["name"] = str(data.get("name") or data.get("fullName") or "无法读取用例结果")
        data["source"] = sources.get(path.name, "")
        data["directory"] = path.parent
        results.append(data)
    return results


def count_statuses(results) -> Counter[str]:
    return Counter(case["status"] for case in results)


def case_duration_ms(case: dict) -> int | None:
    start, stop = case.get("start"), case.get("stop")
    return max(0, stop - start) if isinstance(start, int) and isinstance(stop, int) else None


def describe_counts(counts: Counter[str], *, show_zero: bool = False) -> str:
    return " · ".join(
        f"{label} {counts[status]}" for status, label in STATUS_CN.items()
        if show_zero or counts[status]
    )


def _http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme in ("http", "https") and parsed.hostname and parsed.username is None:
        return value
    return ""


def run_metadata() -> dict[str, str]:
    revision = os.getenv("GITHUB_SHA", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    server = _http_url(os.getenv("GITHUB_SERVER_URL", "https://github.com")).rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    repo_url = f"{server}/{repo}" if server and re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else ""
    commit = revision if re.fullmatch(r"[a-fA-F0-9]{7,64}", revision) else ""
    run_url = f"{repo_url}/actions/runs/{run_id}" if repo_url and run_id.isdigit() else ""
    return {
        "branch": os.getenv("GITHUB_REF_NAME", ""),
        "commit": commit,
        "commit_url": f"{repo_url}/commit/{commit}" if repo_url and commit else "",
        "run_url": run_url,
        "run_id": run_id if run_id.isdigit() else "",
        "attempt": attempt if attempt.isdigit() else "",
    }


def _linked_text(label, url) -> str:
    text = escape(str(label))
    href = _http_url(str(url))
    return f'<a href="{escape(href, quote=True)}">{text}</a>' if href else text


def report_overview(
    counts: Counter[str], context: dict, *, expect_gate: bool = False, compact: bool = False,
) -> str:
    verdict = context.get("verdict")
    has_verdict = isinstance(verdict, str) and verdict in GATE_CN
    if has_verdict:
        title = f"Test Gate 判定：{GATE_CN[verdict]}"
        if verdict == "unverified" and context.get("confirmed_failures") is True:
            title += "（同时存在已确认的测试失败）"
    else:
        title = "Test Gate 判定：未验收" if expect_gate else "未获取门禁结论"

    total = sum(counts.values())
    counts_text = describe_counts(counts, show_zero=True) if total else "无可用结果"
    bits = [
        '<section class="report-overview">',
        f"<h2>{escape(title)}</h2>",
    ]
    if compact:
        bits.append('<details><summary>查看门禁与运行明细</summary>')
    bits.append(f"<p>结果总数 {total} · {escape(counts_text)}</p>")
    if expect_gate and not has_verdict:
        bits.append("<p>未收到有效门禁判定。</p>")

    run = run_metadata()
    if isinstance(context.get("run"), dict):
        run.update(context["run"])
    metadata = []
    if run.get("branch"):
        metadata.append(f"分支 {escape(str(run['branch']))}")
    if run.get("commit"):
        metadata.append("提交 " + _linked_text(run["commit"], run.get("commit_url")))
    if run.get("run_id"):
        metadata.append("运行 " + _linked_text(run["run_id"], run.get("run_url")))
    if run.get("attempt"):
        metadata.append(f"执行尝试 {escape(str(run['attempt']))}")
    if metadata:
        bits.append(f"<p>{' · '.join(metadata)}</p>")

    suites = context.get("suites")
    if has_verdict and isinstance(suites, list) and suites:
        bits.extend([
            '<table class="gate-suites"><thead><tr><th>必跑套件</th><th>判定</th>',
            "<th>执行</th><th>跳过</th><th>失败或错误</th><th>原因</th></tr></thead><tbody>",
        ])
        for suite in suites:
            if isinstance(suite, dict):
                values = (
                    suite.get(key) if suite.get(key) is not None else "—"
                    for key in ("label", "status", "executed", "skipped", "failed", "reason")
                )
                bits.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in values) + "</tr>")
        bits.append("</tbody></table>")
    if compact:
        bits.append("</details>")
    bits.append("</section>")
    return "\n".join(bits)


def case_summary_markdown(results: list[dict]) -> str:
    counts = count_statuses(results)
    lines = ["### 用例统计（来自 Allure 结果）", "", f"- 结果总数 {len(results)}"]
    lines.extend(f"- {label} {counts[key]}" for key, label in STATUS_CN.items())
    failed = [case for case in results if case["status"] in ("failed", "broken")]
    if not results:
        lines.extend(["", "> 无可用结果。请查看必跑套件判定和作业日志。"])
    elif failed:
        lines.extend(["", "### 失败用例", ""])
        for case in failed[:40]:
            name = " ".join(str(case.get("fullName") or case["name"]).splitlines()).replace("`", "'")
            lines.append(f"- `{name}`（{STATUS_CN[case['status']]}）")
        if len(failed) > 40:
            lines.append(f"- 另有 {len(failed) - 40} 条，见 Allure 报告。")
    return "\n".join(lines) + "\n"
