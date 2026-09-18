#!/usr/bin/env python3
"""Generate candidate functional cases from a scanned ticket JSON."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.ci_ai_gen.ticket_pipeline import TicketCaseGenerator
from automation.src.ai_metrics.llm_judge import JudgeUnavailableError, LLMJudgeClient
from automation.src.ticket_pipeline.models import TicketCandidate


def _select_ticket(path: Path, ticket_id: int | None) -> TicketCandidate:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tickets = [TicketCandidate.model_validate(item) for item in payload.get("tickets", [])]
    if ticket_id is not None:
        tickets = [ticket for ticket in tickets if ticket.id == ticket_id]
    if not tickets:
        raise SystemExit("No matching ticket found in candidate file")
    return tickets[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidate-file",
        default="automation/output/ticket-candidates.json",
    )
    parser.add_argument("--ticket-id", type=int)
    parser.add_argument(
        "--out-root",
        default="automation/references/generated-cases/tickets",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ticket = _select_ticket(Path(args.candidate_file), args.ticket_id)
    if args.dry_run:
        print(
            f"Selected ticket {ticket.id} "
            f"({ticket.task_type}, {ticket.project_id}). Dry-run only."
        )
        return 0

    try:
        os.environ.setdefault(
            "LLM_SESSION_ID",
            f"openrobot-ticket-{ticket.id}-{uuid.uuid4().hex[:8]}",
        )
        os.environ.setdefault("LLM_USER_AGENT", "openrobot-test-case-agent/1.0")
        llm = LLMJudgeClient.from_env(project_root=str(Path(__file__).resolve().parents[2]))
    except JudgeUnavailableError as exc:
        print(
            f"LLM unavailable: {exc}\n"
            "Set LLM_API_KEY / LLM_BASE_URL / LLM_MODEL "
            "(or fallback DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL).",
            file=sys.stderr,
        )
        return 2

    generator = TicketCaseGenerator(llm, output_root=Path(args.out_root))
    result = asyncio.run(generator.generate(ticket))
    print(
        f"Generated {result['case_count']} candidate case(s) for ticket "
        f"{result['ticket_id']}: {result['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
