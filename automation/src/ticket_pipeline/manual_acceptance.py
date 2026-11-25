"""Read one production state without running tests or logging ticket content."""

from __future__ import annotations

import json
import os
from pathlib import Path

from automation.src.ticket_pipeline.gate import RESOLVED_STATUSES
from automation.src.ticket_pipeline.scanner import SSHMySQLTicketSource

REQUIRED_ENV = (
    "PROD_TICKET_SSH_KEY",
    "PROD_TICKET_SSH_HOST",
    "PROD_TICKET_SSH_PORT",
    "PROD_TICKET_SSH_USER",
    "PROD_TICKET_DB_USER",
    "PROD_TICKET_DB_PASSWORD",
)


def main() -> int:
    ticket_id = int(os.environ["TICKET_ID"])
    project_id = os.environ["TICKET_PROJECT_ID"]
    if not all(os.environ.get(name, "").strip() for name in REQUIRED_ENV):
        print("::error::Production status lookup is missing CI configuration.")
        return 1
    if not Path(os.environ["PROD_TICKET_SSH_KEY"]).stat().st_size:
        print("::error::Production status lookup is missing its SSH key.")
        return 1
    try:
        status = SSHMySQLTicketSource.from_env().fetch_status_by_id(ticket_id, project_id)
    except Exception as exc:
        print(f"::error::ORS-{ticket_id} production status lookup failed ({type(exc).__name__}).")
        return 1
    eligible = status is not None and status.lower() in RESOLVED_STATUSES
    print(json.dumps({"ticket_id": ticket_id, "production_status": status, "eligible": eligible}))
    if status is None:
        print(f"::error::ORS-{ticket_id} was not found in the production status lookup.")
        return 1
    if not eligible:
        print(f"::notice::ORS-{ticket_id} is not resolved or closed. Functional tests were skipped.")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"eligible={str(eligible).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
