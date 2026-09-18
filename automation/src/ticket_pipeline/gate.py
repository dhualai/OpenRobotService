"""Ticket gate planning and execution for test branch CI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from automation.src.ticket_pipeline.selector import extract_ticket_ids, select_regression_targets


def build_gate_plan(
    message: str,
    *,
    promoted_root: Path,
    regression_targets: Sequence[str],
) -> Dict[str, Any]:
    """Build the pytest command for a commit/PR message."""
    ticket_ids = extract_ticket_ids(message)
    targets = select_regression_targets(message, promoted_root, regression_targets)
    command = [sys.executable, "-m", "pytest", *targets, "-p", "no:cacheprovider", "-q"]
    return {
        "ticket_ids": ticket_ids,
        "targets": targets,
        "command": command,
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
) -> Dict[str, Any]:
    """Execute the gate plan and return a structured result."""
    plan = build_gate_plan(
        message,
        promoted_root=promoted_root,
        regression_targets=regression_targets,
    )
    command = list(plan["command"])
    if junit_path:
        command.extend(["--junitxml", str(junit_path)])
    if allure_dir:
        command.extend(["--alluredir", str(allure_dir)])
    env = os.environ.copy()
    env["AUTOMATION_ENV"] = env_name
    env["PYTHONPATH"] = str(cwd.parent)
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    plan.update(
        {
            "command": command,
            "returncode": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-20000:],
            "stderr": completed.stderr[-20000:],
        }
    )
    return plan