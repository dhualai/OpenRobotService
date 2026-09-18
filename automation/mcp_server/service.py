"""Tool service layer for the OpenRobot MCP server.

This module intentionally has no dependency on the MCP SDK so the logic can
be unit-tested and reused by CLI or future HTTP transports.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from automation.config import load_config
from automation.config.paths import AUTOMATION_ROOT
from automation.src.ai_metrics import JudgeUnavailableError, LLMJudgeClient
from automation.src.client import OpenRobotTestClient
from automation.src.runtime.diagnostics import diagnose_run as runtime_diagnose_run
from automation.src.ticket_pipeline import TicketCandidate
from automation.ci_ai_gen.ticket_pipeline import TicketCaseGenerator

TERMINAL_STATUSES = {"passed", "failed", "stopped", "error"}
DEPENDENCIES = ("tenacity", "pymysql", "redis", "qdrant_client", "playwright", "mcp", "openai")


def _normalize_run(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    run = run or {}
    context = run.get("run") or {}
    status = run.get("status") or {}
    return {
        "run_id": run.get("run_id") or context.get("run_id"),
        "trace_id": context.get("trace_id"),
        "scenario": context.get("scenario"),
        "profile": context.get("profile"),
        "env": context.get("env"),
        "status": status.get("status"),
        "exit_code": status.get("exit_code"),
        "summary": status.get("summary"),
        "started_at": status.get("started_at") or context.get("started_at"),
        "finished_at": status.get("finished_at"),
    }


def run_scenario(
    scenario: str,
    profile: str = "fast",
    env: str = "local",
    variables: Optional[Dict[str, Any]] = None,
    wait: bool = False,
    timeout: float = 1800.0,
    extra_args: Optional[List[str]] = None,
    client: Optional[OpenRobotTestClient] = None,
) -> Dict[str, Any]:
    """Submit a registered scenario, optionally waiting for completion."""
    client = client or OpenRobotTestClient()
    if wait:
        result = client.run(
            scenario=scenario,
            profile=profile,
            env=env,
            variables=variables,
            extra_args=extra_args,
            timeout=timeout,
        )
    else:
        result = client.submit(
            scenario=scenario,
            profile=profile,
            env=env,
            variables=variables,
            extra_args=extra_args,
        )
    normalized = _normalize_run(result)
    normalized["run_dir"] = str(client.store.run_dir(normalized["run_id"])) if normalized.get("run_id") else None
    return normalized


def get_run_status(run_id: str, client: Optional[OpenRobotTestClient] = None) -> Dict[str, Any]:
    """Return the current status and metadata for one run."""
    client = client or OpenRobotTestClient()
    normalized = _normalize_run(client.get_run(run_id))
    normalized["run_dir"] = str(client.store.run_dir(run_id))
    return normalized


def diagnose_run(run_id: str, client: Optional[OpenRobotTestClient] = None) -> Dict[str, Any]:
    """Classify a failed run and return ordered remediation steps."""
    client = client or OpenRobotTestClient()
    return runtime_diagnose_run(run_id, store=client.store)


def inspect_trace(
    run_id: str,
    step_limit: int = 100,
    client: Optional[OpenRobotTestClient] = None,
) -> Dict[str, Any]:
    """Return run metadata, status, steps, and artifacts for one run."""
    client = client or OpenRobotTestClient()
    run = client.get_run(run_id)
    run_dir = client.store.run_dir(run_id)
    artifacts = []
    artifact_dir = run_dir / "artifacts"
    if artifact_dir.is_dir():
        artifacts = [str(path) for path in sorted(artifact_dir.iterdir()) if path.is_file()]
    normalized = _normalize_run(run)
    normalized.update(
        {
            "run_dir": str(run_dir),
            "steps": run.get("steps", [])[: max(1, step_limit)],
            "step_count": len(run.get("steps", [])),
            "artifacts": artifacts,
        }
    )
    return normalized


def _http_check(url: str, timeout: float = 3.0) -> Dict[str, Any]:
    try:
        response = httpx.get(url, timeout=timeout)
        return {"ok": response.status_code == 200, "status_code": response.status_code, "url": url}
    except Exception as exc:  # noqa: BLE001 - diagnostics must report failures
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": url}


def _socket_check(host: str, port: int, timeout: float = 2.0) -> Dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "host": host, "port": port}
    except OSError as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


def diagnose_environment(
    env: str = "local",
    api_base_url: Optional[str] = None,
    ai_base_url: Optional[str] = None,
    check_external: bool = True,
    client: Optional[OpenRobotTestClient] = None,
) -> Dict[str, Any]:
    """Diagnose runtime, dependencies, API, AI, and infrastructure reachability."""
    client = client or OpenRobotTestClient()
    checks: Dict[str, Any] = {
        "runtime": client.health(),
        "python": sys.version.split()[0],
        "dependencies": {name: importlib.util.find_spec(name) is not None for name in DEPENDENCIES},
    }
    try:
        config = load_config(env=env)
        checks["config"] = {"ok": True, "env": config.env}
        api_url = api_base_url or f"{config.api.base_url.rstrip('/')}/api/health"
    except Exception as exc:  # noqa: BLE001
        config = None
        checks["config"] = {"ok": False, "env": env, "error": str(exc)}
        api_url = api_base_url

    if check_external:
        checks["api"] = _http_check(api_url) if api_url else {"ok": False, "error": "api base url unavailable"}
        checks["ai"] = _http_check(ai_base_url or "http://localhost:8401/api/ai/qa/health")
        if config is not None:
            checks["mysql"] = _socket_check(config.database.host, config.database.port)
            checks["redis"] = _socket_check(config.redis.host, config.redis.port)
            checks["qdrant"] = _socket_check(config.qdrant.host, config.qdrant.port)
    else:
        checks["api"] = {"ok": None, "skipped": True}
        checks["ai"] = {"ok": None, "skipped": True}
        checks["mysql"] = {"ok": None, "skipped": True}
        checks["redis"] = {"ok": None, "skipped": True}
        checks["qdrant"] = {"ok": None, "skipped": True}

    runtime_ok = bool(checks["runtime"].get("status") == "healthy")
    dependency_failures = [name for name, ok in checks["dependencies"].items() if not ok]
    external_failures = [
        name
        for name in ("api", "ai", "mysql", "redis", "qdrant")
        if checks.get(name, {}).get("ok") is False
    ]
    if not runtime_ok or not checks["dependencies"].get("pytest", True):
        verdict = "blocked"
    elif dependency_failures or external_failures:
        verdict = "degraded"
    else:
        verdict = "ready"

    next_steps: List[str] = []
    if dependency_failures:
        next_steps.append(f"Install missing Python dependencies: {', '.join(dependency_failures)}")
    if checks.get("api", {}).get("ok") is False:
        next_steps.append("Start the backend or check REAL_API_BASE_URL / config.api.base_url")
    if checks.get("ai", {}).get("ok") is False:
        next_steps.append("Start the AI service on 8401 or set AI_EVAL_BASE_URL")
    for name in ("mysql", "redis", "qdrant"):
        if checks.get(name, {}).get("ok") is False:
            next_steps.append(f"Start {name} or check its host/port in the {env} config")
    if not next_steps:
        next_steps.append("Environment is ready for the selected profile")

    return {
        "env": env,
        "verdict": verdict,
        "checks": checks,
        "next_steps": next_steps,
    }


def run_ai_eval(
    suite: Optional[str] = None,
    profile: str = "pro",
    env: str = "local",
    wait: bool = False,
    timeout: float = 1800.0,
    client: Optional[OpenRobotTestClient] = None,
) -> Dict[str, Any]:
    """Run the AI evaluation scenario, optionally filtering by suite."""
    extra_args = ["-k", suite] if suite else None
    return run_scenario(
        scenario="ai_eval",
        profile=profile,
        env=env,
        wait=wait,
        timeout=timeout,
        extra_args=extra_args,
        client=client,
    )


async def generate_ticket_cases(
    ticket_id: int,
    title: str,
    description: str,
    task_type: str = "feature",
    status: str = "new",
    project_id: str = "Leo_test",
    project_name: str = "摇人吧服务号",
    tags: Optional[List[str]] = None,
    output_root: Optional[Path] = None,
    llm: Optional[LLMJudgeClient] = None,
) -> Dict[str, Any]:
    """Generate candidate cases for one reviewed ticket."""
    ticket = TicketCandidate(
        id=ticket_id,
        title=title,
        description=description,
        task_type=task_type,
        status=status,
        project_id=project_id,
        project_name=project_name,
        tags=tags or ["auto_case"],
    )
    if llm is None:
        try:
            llm = LLMJudgeClient.from_env(project_root=str(AUTOMATION_ROOT.parent))
        except JudgeUnavailableError as exc:
            return {
                "status": "blocked",
                "ticket_id": ticket_id,
                "error": str(exc),
                "next_steps": [
                    "Configure LLM_API_KEY / LLM_BASE_URL / LLM_MODEL or DEEPSEEK_API_KEY",
                    "Re-run generate_ticket_cases after restarting the MCP server",
                ],
            }
    generator = TicketCaseGenerator(llm=llm, output_root=output_root)
    result = await generator.generate(ticket)
    return {"status": "generated", **result}