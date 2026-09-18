#!/usr/bin/env python3
"""Read-only scan production tickets that need automated case generation."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource, TicketScanner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--output",
        default="automation/output/ticket-candidates.json",
        help="UTF-8 JSON output path. Default: %(default)s",
    )
    parser.add_argument("--tag", default="auto_case")
    parser.add_argument("--exclude-creator", action="append", default=[])
    args = parser.parse_args()

    source = SSHMySQLTicketSource.from_env()
    scanner = TicketScanner(
        source,
        project_id="Leo_test",
        statuses=("new",),
        task_types=("feature", "bug"),
        tag=args.tag,
        exclude_creators=args.exclude_creator,
    )
    tickets = scanner.scan(limit=args.limit)
    payload = {
        "project_id": "Leo_test",
        "status": "new",
        "task_types": ["feature", "bug"],
        "tag": args.tag,
        "count": len(tickets),
        "tickets": [ticket.model_dump(mode="json") for ticket in tickets],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"Scanned {len(tickets)} ticket(s). Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
