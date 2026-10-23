"""Ticket gate planning and execution for test branch CI.

Two rules drive this module:

1. **Fail closed.** When the production ticket status cannot be read, the
   functional tests must NOT run. Running them anyway would execute
   requirement tests for a ticket that may not have shipped yet.
2. **Advisory functional failures.** A failing functional case does not block
   the branch by default (the LLM-authored expectations are not authoritative).
   Only a failing regression case blocks; the functional failure is reported
   as a notification instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from automation.src.ticket_pipeline.selector import (
    extract_ticket_ids,
    select_promoted_tests,
)

RESOLVED_STATUSES = ("resolved", "closed")

STATUS_LOOKUP_OK = "ok"
STATUS_LOOKUP_FAILED = "failed"
STATUS_LOOKUP_SKIPPED = "skipped"


def _merge_targets(functional: Sequence[str], regression: Sequence[str]) -> List[str]:
    """Merge target groups, keeping order and dropping duplicates."""
    ordered: List[str] = []
    for item in [*functional, *regression]:
        if item not in ordered:
            ordered.append(item)
    return ordered


def build_gate_plan(
    message: str,
    *,
    promoted_root: Path,
    regression_targets: Sequence[str],
) -> Dict[str, Any]:
    """Build the pytest command for a commit/PR message without status gating."""
    ticket_ids = extract_ticket_ids(message)
    functional = select_promoted_tests(ticket_ids, promoted_root)
    regression = list(regression_targets)
    targets = _merge_targets(functional, regression)
    command = [sys.executable, "-m", "pytest", *targets, "-p", "no:cacheprovider", "-q"]
    return {
        "ticket_ids": ticket_ids,
        "functional_targets": functional,
        "regression_targets": regression,
        "targets": targets,
        "command": command,
        "status_lookup": STATUS_LOOKUP_SKIPPED,
    }


def evaluate_ticket_statuses(
    ticket_ids: Sequence[int],
    statuses: Dict[int, str],
    *,
    resolved_statuses: Sequence[str] = RESOLVED_STATUSES,
) -> Dict[str, Any]:
    """Decide which tickets are eligible for the functional-test gate.

    A ticket qualifies only when its production status is one of
    ``resolved_statuses`` (工单已解决完成). Tickets whose status is unknown are
    not eligible, because the gate must never run functional tests for a
    requirement that has not shipped yet.
    """
    allowed = {item.lower() for item in resolved_statuses}
    eligible: List[int] = []
    rejected: List[Dict[str, Any]] = []
    for ticket_id in ticket_ids:
        status = statuses.get(ticket_id)
        if status is None:
            rejected.append({"ticket_id": ticket_id, "status": None, "reason": "ticket not found"})
        elif status.lower() in allowed:
            eligible.append(ticket_id)
        else:
            rejected.append(
                {
                    "ticket_id": ticket_id,
                    "status": status,
                    "reason": f"status {status!r} is not one of {sorted(allowed)}",
                }
            )
    return {"eligible": eligible, "rejected": rejected}


def build_status_aware_gate_plan(
    message: str,
    *,
    promoted_root: Path,
    regression_targets: Sequence[str],
    statuses: Optional[Dict[int, str]] = None,
    resolved_statuses: Sequence[str] = RESOLVED_STATUSES,
    status_check: bool = False,
) -> Dict[str, Any]:
    """Build a gate plan that only includes functional tests of resolved tickets.

    ``statuses=None`` means the status lookup failed. When ``status_check`` is
    enabled the plan then fails closed: no functional target is selected and
    the plan records ``status_lookup='failed'``. When ``status_check`` is
    disabled the status is simply not consulted, which keeps offline runs
    working.
    """
    ticket_ids = extract_ticket_ids(message)
    check_enabled = bool(status_check) or statuses is not None

    eligible_ids: List[int] = list(ticket_ids)
    rejected: List[Dict[str, Any]] = []
    status_lookup = STATUS_LOOKUP_SKIPPED

    if check_enabled:
        if statuses is None:
            status_lookup = STATUS_LOOKUP_FAILED
            eligible_ids = []
            rejected.append(
                {
                    "ticket_id": None,
                    "status": None,
                    "reason": "ticket status lookup failed",
                }
            )
        else:
            status_lookup = STATUS_LOOKUP_OK
            decision = evaluate_ticket_statuses(
                ticket_ids, statuses, resolved_statuses=resolved_statuses
            )
            eligible_ids = decision["eligible"]
            rejected = decision["rejected"]

    functional = select_promoted_tests(eligible_ids, promoted_root)
    regression = list(regression_targets)
    targets = _merge_targets(functional, regression)
    command = [sys.executable, "-m", "pytest", *targets, "-p", "no:cacheprovider", "-q"]
    plan: Dict[str, Any] = {
        "ticket_ids": ticket_ids,
        "functional_targets": functional,
        "regression_targets": regression,
        "targets": targets,
        "command": command,
        "status_lookup": status_lookup,
    }
    if check_enabled:
        plan["eligible_ticket_ids"] = eligible_ids
        plan["rejected_tickets"] = rejected
    return plan


def _junit_variant(junit_path: Optional[Path], suffix: str) -> Optional[Path]:
    """Derive a per-group junit path from the base path."""
    if junit_path is None:
        return None
    return junit_path.with_name(f"{junit_path.stem}-{suffix}{junit_path.suffix}")


def _run_pytest(
    targets: Sequence[str],
    *,
    cwd: Path,
    env_name: str,
    junit_path: Optional[Path],
    allure_dir: Optional[Path],
    timeout: float,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run one pytest group and describe the outcome."""
    if not targets:
        return {
            "executed": False,
            "passed": True,
            "returncode": 0,
            "targets": [],
            "command": [],
            "stdout": "",
            "stderr": "",
        }
    command = [sys.executable, "-m", "pytest", *targets, "-p", "no:cacheprovider", "-q"]
    if junit_path:
        command.extend(["--junitxml", str(junit_path)])
    if allure_dir:
        command.extend(["--alluredir", str(allure_dir)])
    env = os.environ.copy()
    env["AUTOMATION_ENV"] = env_name
    env["PYTHONPATH"] = str(cwd.parent)
    for key, value in (env_overrides or {}).items():
        env[key] = value
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "executed": True,
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "targets": list(targets),
        "command": command,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }


def run_gate(
    message: str,
    *,
    promoted_root: Path,
    regression_targets: Sequence[str],
    cwd: Path,
    env_name: str = "local",
    junit_path: Optional[Path] = None,
    allure_dir: Optional[Path] = None,
    timeout: float = 1800.0,
    statuses: Optional[Dict[int, str]] = None,
    resolved_statuses: Sequence[str] = RESOLVED_STATUSES,
    status_check: bool = False,
    block_on_functional_failure: bool = False,
    functional_env: Optional[Dict[str, str]] = None,
    regression_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Execute the gate plan and return a structured result.

    The functional group and the regression group run separately so that a
    failing functional case can be reported without blocking the branch, while
    a failing regression case still fails the gate. The two groups may run
    against different backends: generated cases hit the real backend, while
    the regression suite runs against the in-memory mock.
    """
    plan = build_status_aware_gate_plan(
        message,
        promoted_root=promoted_root,
        regression_targets=regression_targets,
        statuses=statuses,
        resolved_statuses=resolved_statuses,
        status_check=status_check,
    )
    functional = _run_pytest(
        plan["functional_targets"],
        cwd=cwd,
        env_name=env_name,
        junit_path=_junit_variant(junit_path, "functional"),
        allure_dir=allure_dir,
        timeout=timeout,
        env_overrides=functional_env,
    )
    regression = _run_pytest(
        plan["regression_targets"],
        cwd=cwd,
        env_name=env_name,
        junit_path=_junit_variant(junit_path, "regression"),
        allure_dir=allure_dir,
        timeout=timeout,
        env_overrides=regression_env,
    )

    functional_failed = bool(functional["executed"]) and not functional["passed"]
    regression_failed = bool(regression["executed"]) and not regression["passed"]
    blocking = regression_failed or (functional_failed and block_on_functional_failure)

    plan.update(
        {
            "functional": functional,
            "regression": regression,
            "functional_failed": functional_failed,
            "regression_failed": regression_failed,
            "blocking": blocking,
            "passed": not blocking,
        }
    )
    return plan
