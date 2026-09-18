"""CLI for the ticket-driven test pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

AUTOMATION_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AUTOMATION_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.ci_ai_gen.ticket_pipeline import TicketCaseGenerator  # noqa: E402
from automation.src.ai_metrics import LLMJudgeClient  # noqa: E402
from automation.src.ticket_pipeline import TicketPipeline, TicketScanner  # noqa: E402
from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource  # noqa: E402
from automation.src.ticket_pipeline.selector import (  # noqa: E402
    extract_ticket_ids,
    select_regression_targets,
)


def _print(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _scanner():
    return TicketScanner(SSHMySQLTicketSource.from_env())


def _generator(output_root=None):
    llm = LLMJudgeClient.from_env(project_root=str(REPO_ROOT))
    return TicketCaseGenerator(llm=llm, output_root=output_root)


def build_parser():
    parser = argparse.ArgumentParser(description="Ticket-driven test pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--limit", type=int, default=100)

    gen = sub.add_parser("generate")
    gen.add_argument("--ticket-id", type=int, required=True)
    gen.add_argument("--title", required=True)
    gen.add_argument("--description", required=True)
    gen.add_argument("--task-type", default="feature")
    gen.add_argument("--project-id", default="Leo_test")
    gen.add_argument("--project-name", default="摇人吧服务号")
    gen.add_argument("--output-root", default="")

    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=100)
    run.add_argument("--ticket-id", action="append", type=int, default=[])
    run.add_argument("--output-root", default="")

    promote = sub.add_parser("promote")
    promote.add_argument("--ticket-id", type=int, required=True)
    promote.add_argument("--candidate-root", default="")
    promote.add_argument("--promoted-root", default="")
    promote.add_argument("--allow-unapproved", action="store_true")

    select = sub.add_parser("select")
    select.add_argument("--text", default="")
    select.add_argument("--file", default="")
    select.add_argument("--promoted-root", default="")
    select.add_argument("--regression", action="append", default=[])
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        _print([ticket.model_dump(mode="json") for ticket in _scanner().scan(limit=args.limit)])
        return 0

    if args.command == "generate":
        generator = _generator(Path(args.output_root) if args.output_root else None)
        from automation.src.ticket_pipeline.models import TicketCandidate

        ticket = TicketCandidate(
            id=args.ticket_id,
            title=args.title,
            description=args.description,
            task_type=args.task_type,
            status="new",
            project_id=args.project_id,
            project_name=args.project_name,
            tags=["auto_case"],
        )
        _print(asyncio.run(generator.generate(ticket)))
        return 0

    if args.command == "run":
        output_root = Path(args.output_root) if args.output_root else None
        pipeline = TicketPipeline(_scanner(), _generator(output_root))
        _print(asyncio.run(pipeline.run(limit=args.limit, ticket_ids=args.ticket_id or None)))
        return 0

    if args.command == "promote":
        candidate_root = Path(args.candidate_root) if args.candidate_root else AUTOMATION_ROOT / "references" / "generated-cases" / "tickets"
        promoted_root = Path(args.promoted_root) if args.promoted_root else AUTOMATION_ROOT / "references" / "promoted-cases"
        pipeline = TicketPipeline(
            scanner=SimpleNamespace(scan=lambda limit=100: []),
            generator=SimpleNamespace(output_root=candidate_root),
            promoted_root=promoted_root,
        )
        _print(pipeline.promote(args.ticket_id, require_approved=not args.allow_unapproved))
        return 0

    if args.command == "select":
        text = args.text
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        promoted_root = Path(args.promoted_root) if args.promoted_root else AUTOMATION_ROOT / "references" / "promoted-cases"
        _print(
            {
                "ticket_ids": extract_ticket_ids(text),
                "targets": select_regression_targets(
                    text,
                    promoted_root=promoted_root,
                    regression_targets=args.regression,
                ),
            }
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())