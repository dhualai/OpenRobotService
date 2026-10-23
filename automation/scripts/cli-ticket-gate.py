"""Run the ticket gate for a commit or PR message."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

AUTOMATION_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTOMATION_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.src.ticket_pipeline.gate import run_gate  # noqa: E402
from automation.src.ticket_pipeline.notification import (  # noqa: E402
    build_notification,
    emit_github_annotations,
    post_ticket_comment,
    render_comment,
    write_notification,
)
from automation.src.ticket_pipeline.selector import extract_ticket_ids  # noqa: E402

DEFAULT_REGRESSION = [
    "tests/business_chain",
    "tests/tasks",
    "tests/call",
    "tests/admin",
    "tests/auth",
]


def _load_statuses(ticket_ids):
    """Fetch production ticket statuses.

    Returns ``(statuses, error)``. ``statuses`` is ``None`` when the lookup
    failed; the caller must then fail closed instead of letting the functional
    tests run.
    """
    if not ticket_ids:
        return {}, None
    if os.getenv("TICKET_STATUS_LOOKUP_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return None, "ticket status lookup disabled (missing production credentials)"
    try:
        from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource

        source = SSHMySQLTicketSource.from_env()
        return {ticket.id: ticket.status for ticket in source.fetch_by_ids(ticket_ids)}, None
    except Exception as exc:  # noqa: BLE001 - the gate decides how to handle it
        return None, str(exc)


def build_parser():
    parser = argparse.ArgumentParser(description="Ticket gate for test branch")
    parser.add_argument("--message-file", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--promoted-root", default="")
    parser.add_argument("--regression", action="append", default=[])
    parser.add_argument("--env", default="local")
    parser.add_argument("--junit", default=str(AUTOMATION_ROOT / "output" / "junit-ticket-gate.xml"))
    parser.add_argument("--allure-dir", default=str(AUTOMATION_ROOT / "output" / "allure-results"))
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument(
        "--require-resolved",
        action="store_true",
        help="Only run functional tests of tickets whose production status is resolved/closed.",
    )
    parser.add_argument(
        "--resolved-statuses",
        default="resolved,closed",
        help="Comma separated list of ticket statuses that count as 已解决完成.",
    )
    parser.add_argument(
        "--block-on-functional-failure",
        action="store_true",
        help="Let a failing functional case block the branch (default: notify only).",
    )
    parser.add_argument(
        "--notification-file",
        default=str(AUTOMATION_ROOT / "output" / "ticket-gate-notification.json"),
        help="Where the failure notification payload is written.",
    )
    parser.add_argument(
        "--comment-api-base-url",
        default=os.getenv("TICKET_COMMENT_API_BASE_URL", ""),
        help="Ticket system base URL used to post the result comment (optional).",
    )
    parser.add_argument(
        "--report-url",
        default=os.getenv("ALLURE_REPORT_URL", ""),
        help="Link to the test report included in the notification.",
    )
    parser.add_argument(
        "--functional-use-mock",
        default=os.getenv("TICKET_GATE_FUNCTIONAL_USE_MOCK", "0"),
        help="USE_MOCK for the generated functional cases (0 = real backend).",
    )
    parser.add_argument(
        "--regression-use-mock",
        default=os.getenv("TICKET_GATE_REGRESSION_USE_MOCK", "1"),
        help="USE_MOCK for the regression suite (1 = in-memory mock backend).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    message = args.text
    if args.message_file:
        message = Path(args.message_file).read_text(encoding="utf-8")
    promoted_root = (
        Path(args.promoted_root)
        if args.promoted_root
        else AUTOMATION_ROOT / "references" / "promoted-cases"
    )
    regression = args.regression or DEFAULT_REGRESSION
    resolved_statuses = [item.strip() for item in args.resolved_statuses.split(",") if item.strip()]

    ticket_ids = extract_ticket_ids(message)
    statuses = None
    status_error = None
    if args.require_resolved:
        statuses, status_error = _load_statuses(ticket_ids)
        if status_error:
            print(f"::warning::could not read ticket statuses: {status_error}")
            print("::warning::工单状态不可用，按 fail-closed 处理：跳过功能用例，仅执行回归")

    result = run_gate(
        message,
        promoted_root=promoted_root,
        regression_targets=regression,
        cwd=AUTOMATION_ROOT,
        env_name=args.env,
        junit_path=Path(args.junit),
        allure_dir=Path(args.allure_dir),
        timeout=args.timeout,
        statuses=statuses,
        resolved_statuses=resolved_statuses,
        status_check=args.require_resolved,
        block_on_functional_failure=args.block_on_functional_failure,
        functional_env={"USE_MOCK": args.functional_use_mock},
        regression_env={"USE_MOCK": args.regression_use_mock},
    )

    if result.get("functional_failed"):
        notification = build_notification(
            ticket_ids=result.get("eligible_ticket_ids") or result.get("ticket_ids") or [],
            functional=result.get("functional") or {},
            status_lookup=result.get("status_lookup", "ok"),
            rejected_tickets=result.get("rejected_tickets"),
            report_url=args.report_url,
        )
        emit_github_annotations(notification)
        write_notification(Path(args.notification_file), notification)
        if args.comment_api_base_url:
            comment = render_comment(notification)
            comment_results = []
            for ticket_id in notification["ticket_ids"]:
                posted = post_ticket_comment(args.comment_api_base_url, ticket_id, comment)
                comment_results.append({"ticket_id": ticket_id, **posted})
            notification["comment_results"] = comment_results
        result["notification"] = notification

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
