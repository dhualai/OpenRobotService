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
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    try:
        print(text)
    except UnicodeEncodeError:
        # GBK 控制台（Windows PowerShell 默认）遇到 emoji 等字符会编码失败，
        # 降级为"替换不可编码字符"后再输出，保证 CLI 不因打印崩溃。
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding))


def _load_spec(spec_dir):
    """Load the OpenAPI spec and endpoint list used to ground generated cases."""
    if not spec_dir:
        return None, []
    root = Path(spec_dir)
    spec = None
    spec_path = root / "openapi.json"
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    endpoints = []
    endpoints_path = root / "endpoints.json"
    if endpoints_path.is_file():
        endpoints = json.loads(endpoints_path.read_text(encoding="utf-8")).get("endpoints", [])
    return spec, endpoints


def _scanner(args):
    source = SSHMySQLTicketSource.from_env()
    statuses = _split_csv(getattr(args, "statuses", "")) or list(source.statuses)
    task_types = _split_csv(getattr(args, "task_types", "")) or list(source.task_types)
    tag = getattr(args, "tag", None)
    if tag is None:
        tag = source.tag
    exclude_sources = _split_csv(getattr(args, "exclude_sources", ""))
    if not exclude_sources:
        exclude_sources = list(source.exclude_sources)
    return TicketScanner(
        source,
        project_id=getattr(args, "project_id", "") or source.project_id,
        statuses=tuple(statuses),
        task_types=tuple(task_types),
        tag=tag,
        exclude_sources=tuple(exclude_sources),
    )


def _generator(output_root=None, spec_dir=None):
    llm = LLMJudgeClient.from_env(project_root=str(REPO_ROOT))
    spec, endpoints = _load_spec(spec_dir)
    return TicketCaseGenerator(
        llm=llm,
        output_root=output_root,
        spec=spec,
        endpoints=endpoints,
    )


def build_parser():
    parser = argparse.ArgumentParser(description="Ticket-driven test pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--limit", type=int, default=100)
    scan.add_argument("--project-id", default="")
    scan.add_argument("--statuses", default="", help="Comma separated ticket statuses, e.g. new")
    scan.add_argument("--task-types", default="", help="Comma separated ticket types, e.g. feature")
    scan.add_argument("--tag", default=None, help="Required tag; pass an empty value to disable tag filtering")
    scan.add_argument(
        "--exclude-sources",
        default="",
        help="Comma separated ticket sources to skip, e.g. ai",
    )

    get = sub.add_parser("get")
    get.add_argument("--ticket-id", type=int, required=True, help="Ticket id (tasks.id) to fetch")

    gen = sub.add_parser("generate")
    gen.add_argument("--ticket-id", type=int, required=True)
    gen.add_argument("--title", required=True)
    gen.add_argument("--description", required=True)
    gen.add_argument("--task-type", default="feature")
    gen.add_argument("--project-id", default="Leo_test")
    gen.add_argument("--project-name", default="摇人吧服务号")
    gen.add_argument("--output-root", default="")
    gen.add_argument("--spec-dir", default="", help="Directory holding openapi.json and endpoints.json")

    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=100)
    run.add_argument("--ticket-id", action="append", type=int, default=[])
    run.add_argument("--output-root", default="")
    run.add_argument("--spec-dir", default="", help="Directory holding openapi.json and endpoints.json")
    run.add_argument("--project-id", default="")
    run.add_argument("--statuses", default="", help="Comma separated ticket statuses, e.g. new")
    run.add_argument("--task-types", default="", help="Comma separated ticket types, e.g. feature")
    run.add_argument("--tag", default=None, help="Required tag; pass an empty value to disable tag filtering")
    run.add_argument(
        "--exclude-sources",
        default="",
        help="Comma separated ticket sources to skip, e.g. ai",
    )
    run.add_argument("--force", action="store_true", help="Regenerate candidates that already exist")

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


def _split_csv(value):
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        _print([ticket.model_dump(mode="json") for ticket in _scanner(args).scan(limit=args.limit)])
        return 0

    if args.command == "get":
        source = SSHMySQLTicketSource.from_env()
        _print([ticket.model_dump(mode="json") for ticket in source.fetch_by_ids([args.ticket_id])])
        return 0

    if args.command == "generate":
        generator = _generator(Path(args.output_root) if args.output_root else None, args.spec_dir)
        from automation.src.ticket_pipeline.models import TicketCandidate

        ticket = TicketCandidate(
            id=args.ticket_id,
            title=args.title,
            description=args.description,
            task_type=args.task_type,
            status="new",
            project_id=args.project_id,
            project_name=args.project_name,
            tags=[],
        )
        _print(asyncio.run(generator.generate(ticket)))
        return 0

    if args.command == "run":
        output_root = Path(args.output_root) if args.output_root else None
        pipeline = TicketPipeline(_scanner(args), _generator(output_root, args.spec_dir))
        _print(
            asyncio.run(
                pipeline.run(
                    limit=args.limit,
                    ticket_ids=args.ticket_id or None,
                    force=args.force,
                )
            )
        )
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
