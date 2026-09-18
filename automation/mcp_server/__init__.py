"""OpenRobot MCP server package."""

from automation.mcp_server.service import (
    diagnose_environment,
    diagnose_run,
    generate_ticket_cases,
    get_run_status,
    inspect_trace,
    run_ai_eval,
    run_scenario,
)

__all__ = [
    "diagnose_environment",
    "diagnose_run",
    "generate_ticket_cases",
    "get_run_status",
    "inspect_trace",
    "run_ai_eval",
    "run_scenario",
]