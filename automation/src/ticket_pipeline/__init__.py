"""Ticket-driven automation pipeline primitives."""

from automation.src.ticket_pipeline.models import TicketCandidate
from automation.src.ticket_pipeline.gate import (
    build_gate_plan,
    build_status_aware_gate_plan,
    evaluate_ticket_statuses,
    run_gate,
)
from automation.src.ticket_pipeline.notification import (
    build_notification,
    emit_github_annotations,
    extract_failed_tests,
    post_ticket_comment,
    render_comment,
)
from automation.src.ticket_pipeline.pipeline import TicketPipeline, TicketPipelineResult
from automation.src.ticket_pipeline.scanner import TicketScanner
from automation.src.ticket_pipeline.script_renderer import (
    render_script,
    validate_case_requests,
)
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
    "build_status_aware_gate_plan",
    "build_notification",
    "emit_github_annotations",
    "evaluate_ticket_statuses",
    "extract_failed_tests",
    "post_ticket_comment",
    "render_comment",
    "render_script",
    "run_gate",
    "TicketPipelineResult",
    "extract_ticket_ids",
    "select_promoted_tests",
    "select_regression_targets",
    "validate_case_requests",
]
