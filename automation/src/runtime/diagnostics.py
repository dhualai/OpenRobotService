"""Failure classification and self-diagnosis for runtime runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from automation.src.runtime.trace_store import TraceStore


def _find_text(status: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    parts = [str(status.get("summary") or "")]
    for step in steps:
        parts.append(str(step.get("error") or ""))
        parts.append(str(step.get("detail") or ""))
        parts.append(str(step.get("step") or ""))
    return "\n".join(parts).lower()


def classify_failure(status: Dict[str, Any], steps: List[Dict[str, Any]]) -> Optional[str]:
    """Classify a failed run using status and step evidence."""
    run_status = status.get("status")
    if run_status == "passed":
        return None
    text = _find_text(status, steps)

    if any(token in text for token in ("no module named", "modulenotfounderror", "importerror")):
        return "dependency_missing"
    if any(token in text for token in ("connection refused", "connection reset", "unreachable", "name or service not known", "max retries")):
        return "environment_unreachable"
    if any(token in text for token in ("timed out", "timeout", "timeoutexpired", "timeout after")):
        return "timeout"
    if any(token in text for token in ("assertionerror", "assert ", "failed: expected", "mismatch")):
        return "assertion_failure"
    if any(token in text for token in ("ai service", "judge unavailable", "llm_api_key", "deepseek")):
        return "ai_service_unavailable"
    if any(token in text for token in ("permission denied", "access denied")):
        return "permission_error"
    return "unknown"


_NEXT_STEPS = {
    "dependency_missing": [
        "Run pip install -e automation/",
        "Install optional AI dependencies with pip install -e \"automation/[external-eval]\"",
    ],
    "environment_unreachable": [
        "Run diagnose_environment for the target environment",
        "Start the backend/AI service or open the SSH tunnel",
    ],
    "timeout": [
        "Inspect the slowest step in inspect_trace",
        "Increase the scenario timeout or split the long-running test",
    ],
    "assertion_failure": [
        "Inspect the failed step and request/response artifacts",
        "Check backend state and test data before rerunning",
    ],
    "ai_service_unavailable": [
        "Check AI_EVAL_BASE_URL and the AI service health endpoint",
        "Configure LLM_API_KEY or DEEPSEEK_API_KEY for judge/evals",
    ],
    "permission_error": [
        "Check the writable run directory and file permissions",
        "Run the scenario from a writable working directory",
    ],
    "unknown": [
        "Run inspect_trace and inspect the pytest.log artifact",
        "Run diagnose_environment before retrying",
    ],
}


def diagnose_run(run_id: str, store: Optional[TraceStore] = None) -> Dict[str, Any]:
    """Classify a run and return ordered remediation steps."""
    store = store or TraceStore()
    status = store.read_status(run_id) or {}
    steps = store.read_steps(run_id)
    failure_class = classify_failure(status, steps)
    next_steps = _NEXT_STEPS.get(failure_class or "unknown", _NEXT_STEPS["unknown"])
    run_dir = store.run_dir(run_id)
    evidence = [
        step for step in steps
        if step.get("status") in ("failed", "error") or step.get("error")
    ][-5:]
    return {
        "run_id": run_id,
        "trace_id": status.get("trace_id"),
        "status": status.get("status"),
        "failure_class": failure_class,
        "evidence": evidence,
        "next_steps": next_steps if status.get("status") != "passed" else [],
        "run_dir": str(run_dir),
        "log_path": str(Path(run_dir) / "artifacts" / "pytest.log"),
    }