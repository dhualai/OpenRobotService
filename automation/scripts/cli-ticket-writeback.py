"""Write an automation run report back to one ticket."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.src.reporting.ticket_writeback import writeback_ticket_report  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write automation result back to a ticket")
    parser.add_argument("--ticket-id", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args(argv)

    result = asyncio.run(writeback_ticket_report(
        args.ticket_id,
        args.run_id,
        username=args.username or None,
        password=args.password or None,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "updated" else 1


if __name__ == "__main__":
    raise SystemExit(main())