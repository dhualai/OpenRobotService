"""OpenRobot test runtime primitives."""

from automation.src.runtime.context import RunContext, new_run_id, new_trace_id, utc_now_iso
from automation.src.runtime.diagnostics import classify_failure, diagnose_run
from automation.src.runtime.environments import EnvironmentPool, EnvironmentProfile
from automation.src.runtime.manager import RunManager
from automation.src.runtime.scheduler import RunScheduler, ScheduledTask
from automation.src.runtime.trace_store import TERMINAL_STATUSES, TraceStore

__all__ = [
    "RunContext",
    "classify_failure",
    "diagnose_run",
    "RunManager",
    "EnvironmentPool",
    "EnvironmentProfile",
    "RunScheduler",
    "ScheduledTask",
    "TraceStore",
    "TERMINAL_STATUSES",
    "new_run_id",
    "new_trace_id",
    "utc_now_iso",
]