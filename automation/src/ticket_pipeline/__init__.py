"""Ticket-driven automation pipeline primitives."""

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.gate import build_gate_plan, run_gate
from automation.src.ticket_pipeline.pipeline import TicketPipeline, TicketPipelineResult
from automation.src.ticket_pipeline.scanner import TicketScanner
from automation.src.ticket_pipeline.selector import (
    extract_ticket_ids,
    select_promoted_tests,
    select_regression_targets,
)

__all__ = [
    "TicketCandidate",
    "TicketScanner",
    "TicketPipeline",
    "build_gate_plan",
    "run_gate",
    "TicketPipelineResult",
    "extract_ticket_ids",
    "select_promoted_tests",
    "select_regression_targets",
]
