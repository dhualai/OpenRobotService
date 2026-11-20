"""Notification payloads for ticket gate outcomes.

The gate treats a failing functional case as advisory: the branch is not
blocked, a human decides whether the failure is a real defect. This module
turns the failed run into a payload that can be posted as a ticket comment and
surfaced as a GitHub Actions annotation.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)


def extract_failed_tests(output: str) -> List[str]:
    """Extract the node ids of failing tests from pytest output."""
    failed: List[str] = []
    for match in _FAILED_LINE_RE.finditer(output or ""):
        node_id = match.group(1)
        if node_id not in failed:
            failed.append(node_id)
    return failed


def build_notification(
    *,
    ticket_ids: Sequence[int],
    functional: Dict[str, Any],
    status_lookup: str = "ok",
    rejected_tickets: Optional[Sequence[Dict[str, Any]]] = None,
    report_url: str = "",
) -> Dict[str, Any]:
    """Build the notification payload for a failed functional run."""
    output = f"{functional.get('stdout', '')}\n{functional.get('stderr', '')}"
    failed_tests = extract_failed_tests(output)
    ticket_refs = ", ".join(f"ORS-{ticket_id}" for ticket_id in ticket_ids) or "(none)"
    if failed_tests:
        summary = (
            f"自动化功能用例未通过（{len(failed_tests)} 个用例失败），"
            f"相关工单：{ticket_refs}。按「只通知不阻断」策略，门禁未阻断分支，请人工确认是否为真实缺陷。"
        )
    else:
        summary = (
            f"自动化功能用例执行失败（退出码 {functional.get('returncode')}），"
            f"相关工单：{ticket_refs}。按「只通知不阻断」策略，门禁未阻断分支，请人工确认。"
        )
    return {
        "status": "functional_failed",
        "ticket_ids": list(ticket_ids),
        "ticket_refs": ticket_refs,
        "failed_tests": failed_tests,
        "summary": summary,
        "report_url": report_url,
        "status_lookup": status_lookup,
        "rejected_tickets": list(rejected_tickets or []),
    }


def render_comment(notification: Dict[str, Any]) -> str:
    """Render the notification as a Markdown comment body."""
    lines = ["## 自动化功能用例未通过", "", notification.get("summary", "")]
    failed_tests = notification.get("failed_tests") or []
    if failed_tests:
        lines.append("")
        lines.append("失败用例：")
        lines.extend(f"- `{node_id}`" for node_id in failed_tests)
    report_url = notification.get("report_url")
    if report_url:
        lines.append("")
        lines.append(f"报告：{report_url}")
    return "\n".join(lines) + "\n"


def emit_github_annotations(notification: Dict[str, Any]) -> None:
    """Print GitHub Actions warning annotations for the notification."""
    summary = notification.get("summary", "")
    if not summary:
        return
    print(f"::warning::{summary}")
    for node_id in notification.get("failed_tests") or []:
        print(f"::warning::failed test: {node_id}")


def write_notification(path: Path, notification: Dict[str, Any]) -> None:
    """Persist the notification payload as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(notification, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _api_root(base_url: str) -> str:
    """Return the server root, tolerating a base URL that already has /api."""
    root = base_url.rstrip("/")
    if root.endswith("/api"):
        return root[: -len("/api")]
    return root


def post_ticket_comment(
    base_url: str,
    ticket_id: int,
    content: str,
    *,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Post a comment to one ticket. Returns a status dict and never raises."""
    username = username or os.getenv("AUTOMATION_BOT_USERNAME", "")
    password = password or os.getenv("AUTOMATION_BOT_PASSWORD", "")
    if not username or not password:
        return {
            "status": "skipped",
            "reason": "AUTOMATION_BOT_USERNAME/PASSWORD are required to post a comment",
        }
    root = _api_root(base_url)
    url = f"{root}/api/tasks/{ticket_id}/comments"
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            login = client.post(
                f"{root}/api/auth/login",
                json={"username": username, "password": password},
            )
            if login.status_code != 200:
                return {
                    "status": "failed",
                    "reason": f"login failed with HTTP {login.status_code}",
                    "response": login.text[:500],
                }
            token = login.json().get("access_token")
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"content": content},
            )
        return {
            "status": "posted" if response.status_code == 200 else "failed",
            "http_status": response.status_code,
            "url": url,
            "response": response.text[:500],
        }
    except Exception as exc:  # noqa: BLE001 - notification must never break the gate
        return {"status": "failed", "reason": str(exc)}
