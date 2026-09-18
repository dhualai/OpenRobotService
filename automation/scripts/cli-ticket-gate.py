"""Run the ticket gate for a commit or PR message."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUTOMATION_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTOMATION_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.src.ticket_pipeline.gate import run_gate  # noqa: E402


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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    message = args.text
    if args.message_file:
        message = Path(args.message_file).read_text(encoding="utf-8")
    promoted_root = Path(args.promoted_root) if args.promoted_root else AUTOMATION_ROOT / "references" / "promoted-cases"
    regression = args.regression or ["tests/business_chain", "tests/tasks"]
    result = run_gate(
        message,
        promoted_root=promoted_root,
        regression_targets=regression,
        cwd=AUTOMATION_ROOT,
        env_name=args.env,
        junit_path=Path(args.junit),
        allure_dir=Path(args.allure_dir),
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())