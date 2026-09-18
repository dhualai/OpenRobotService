"""Thin client for the OpenRobot test runtime.

Phase 0 is file-backed and local: the client creates a run, starts pytest as
a subprocess, and exposes run/status/trace through the shared TraceStore.
The same interface can later be served by the MCP server or a remote daemon.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from automation.config.paths import AUTOMATION_ROOT
from automation.src.runtime import RunManager, TraceStore, TERMINAL_STATUSES


@dataclass(frozen=True)
class Scenario:
    """A named pytest target that can be run through the client."""

    name: str
    targets: tuple[str, ...]
    profiles: tuple[str, ...] = ("fast",)
    description: str = ""


SCENARIOS: Dict[str, Scenario] = {
    "framework": Scenario(
        "framework",
        ("src", "config", "ci_ai_gen"),
        ("fast",),
        "Framework, configuration, and CI generator tests",
    ),
    "api_mock": Scenario(
        "api_mock",
        ("tests", "-m", "api", "--ignore=tests/ai", "--ignore=tests/real", "--ignore=tests/infrastructure"),
        ("fast",),
        "Mock API suite and business-chain tests",
    ),
    "business_chain": Scenario(
        "business_chain",
        ("tests/business_chain",),
        ("fast", "pro"),
        "Call-to-ticket close business chain",
    ),
    "contract": Scenario(
        "contract",
        ("tests/contract", "-m", "contract"),
        ("pro", "nightly"),
        "OpenAPI contract smoke via Schemathesis",
    ),
    "real_smoke": Scenario(
        "real_smoke",
        ("tests/real", "-m", "smoke"),
        ("pro",),
        "Real backend smoke tests",
    ),
    "real_lifecycle": Scenario(
        "real_lifecycle",
        ("tests/real/test_ticket_lifecycle_real.py",),
        ("pro",),
        "Real backend ticket lifecycle",
    ),
    "infrastructure": Scenario(
        "infrastructure",
        ("tests/infrastructure", "-m", "db"),
        ("pro",),
        "MySQL, Redis, and Qdrant contract checks",
    ),
    "ai_eval": Scenario(
        "ai_eval",
        ("tests/ai", "-m", "ai"),
        ("pro", "nightly"),
        "AI quality evaluation (L1/L2/L3)",
    ),
    "ui_smoke": Scenario(
        "ui_smoke",
        ("tests/ui", "-m", "ui"),
        ("pro", "nightly"),
        "Playwright UI smoke tests",
    ),
    "ui_e2e": Scenario(
        "ui_e2e",
        ("tests/ui", "-m", "ui and e2e"),
        ("pro", "nightly"),
        "Opt-in Playwright login and Call workspace flow",
    ),
}

ALL_PROFILES = ("fast", "pro", "nightly")


class OpenRobotTestClient:
    """Local Phase 0 runtime client."""

    def __init__(
        self,
        automation_root: Optional[Path] = None,
        run_root: Optional[Path] = None,
        python_executable: Optional[str] = None,
    ):
        self.automation_root = Path(automation_root or AUTOMATION_ROOT).resolve()
        self.run_root = Path(run_root) if run_root else self.automation_root / "output" / "runs"
        self.python_executable = python_executable or sys.executable
        self.store = TraceStore(self.run_root)
        self.manager = RunManager(self.store)
        self._processes: Dict[str, subprocess.Popen] = {}
        self._log_files: Dict[str, Any] = {}

    # ------------------------------------------------------------------ info
    def health(self) -> Dict[str, Any]:
        checks = {
            "automation_root": self.automation_root.is_dir(),
            "pytest_installed": importlib.util.find_spec("pytest") is not None,
            "run_root": self.run_root.exists() or self._can_create(self.run_root),
        }
        status = "healthy" if all(checks.values()) else "degraded"
        return {"status": status, "checks": checks, "run_root": str(self.run_root)}

    def capabilities(self) -> Dict[str, Any]:
        return {
            "runtime": "openrobot-local",
            "phase": "0",
            "profiles": list(ALL_PROFILES),
            "scenarios": [
                {
                    "name": scenario.name,
                    "profiles": list(scenario.profiles),
                    "description": scenario.description,
                    "targets": list(scenario.targets),
                }
                for scenario in SCENARIOS.values()
            ],
        }

    def list_scenarios(self) -> List[Scenario]:
        return list(SCENARIOS.values())

    def get_scenario(self, name: str) -> Scenario:
        try:
            return SCENARIOS[name]
        except KeyError as exc:
            known = ", ".join(sorted(SCENARIOS))
            raise ValueError(f"Unknown scenario: {name}. Known: {known}") from exc

    # ------------------------------------------------------------------ runs
    def submit(
        self,
        scenario: str,
        profile: str = "fast",
        env: str = "local",
        variables: Optional[Dict[str, Any]] = None,
        extra_args: Optional[Sequence[str]] = None,
        run_id: Optional[str] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        selected = self.get_scenario(scenario)
        self._validate_profile(selected, profile)

        context = self.manager.create_run(
            scenario=selected.name,
            profile=profile,
            env=env,
            metadata={"variables": dict(variables or {}), "env_overrides": dict(env_overrides or {})},
            run_id=run_id,
        )
        command = self._build_command(selected, profile, env, extra_args)
        run_dir = self.store.run_dir(context.run_id)
        log_path = run_dir / "artifacts" / "pytest.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.update_run(
            context.run_id,
            command=command,
            cwd=str(self.automation_root),
            python=self.python_executable,
            variables=dict(variables or {}),
        )

        log_file = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.automation_root),
                env=self._child_env(context, variables or {}, env_overrides or {}),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            log_file.close()
            self.store.finish_run(context.run_id, "failed", summary=str(exc))
            raise RuntimeError(f"Failed to start scenario {scenario}: {exc}") from exc

        self._processes[context.run_id] = process
        self._log_files[context.run_id] = log_file
        self.store.update_status(context.run_id, "running", pid=process.pid)
        return self.get_run(context.run_id)

    def get_run(self, run_id: str) -> Dict[str, Any]:
        self._refresh_process_state(run_id)
        return self.manager.get_run(run_id)

    def wait_for_run(self, run_id: str, timeout: float = 1800.0, poll_interval: float = 1.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.get_run(run_id)
            status = (run.get("status") or {}).get("status")
            if status in TERMINAL_STATUSES:
                return run
            time.sleep(poll_interval)
        self.store.finish_run(run_id, "failed", summary=f"timeout after {timeout:.0f}s")
        return self.get_run(run_id)

    def run(
        self,
        scenario: str,
        profile: str = "fast",
        env: str = "local",
        variables: Optional[Dict[str, Any]] = None,
        extra_args: Optional[Sequence[str]] = None,
        timeout: float = 1800.0,
        run_id: Optional[str] = None,
        env_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        submitted = self.submit(
            scenario=scenario,
            profile=profile,
            env=env,
            variables=variables,
            extra_args=extra_args,
            run_id=run_id,
            env_overrides=env_overrides,
        )
        return self.wait_for_run(submitted["run_id"], timeout=timeout)

    def stop(self, run_id: str) -> Dict[str, Any]:
        process = self._processes.pop(run_id, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self._close_log(run_id)
        self.store.finish_run(run_id, "stopped", summary="stopped by client")
        return self.get_run(run_id)

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.manager.list_runs(limit=limit)

    # --------------------------------------------------------------- helpers
    def _validate_profile(self, scenario: Scenario, profile: str) -> None:
        if profile == "nightly" and "pro" in scenario.profiles:
            return
        if profile not in scenario.profiles:
            raise ValueError(
                f"Profile {profile!r} is not valid for scenario {scenario.name!r}; "
                f"allowed: {', '.join(scenario.profiles)}"
            )

    def _build_command(
        self,
        scenario: Scenario,
        profile: str,
        env: str,
        extra_args: Optional[Sequence[str]],
    ) -> List[str]:
        command = [
            self.python_executable,
            "-m",
            "pytest",
            *scenario.targets,
            f"--run-scenario={scenario.name}",
            f"--run-profile={profile}",
            f"--run-env={env}",
            "-p",
            "no:cacheprovider",
            "-q",
        ]
        command.extend(extra_args or [])
        return command

    @staticmethod
    def _child_env(context, variables: Dict[str, Any], env_overrides: Dict[str, str]) -> Dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "OPENROBOT_RUN_ID": context.run_id,
                "OPENROBOT_TRACE_ID": context.trace_id,
                "OPENROBOT_SCENARIO": context.scenario,
                "OPENROBOT_PROFILE": context.profile,
                "OPENROBOT_ENV": context.env,
            }
        )
        env.update({key: str(value) for key, value in env_overrides.items()})
        for key, value in variables.items():
            env[f"OPENROBOT_VAR_{key.upper()}"] = json.dumps(value, ensure_ascii=False)
        return env

    def _refresh_process_state(self, run_id: str) -> None:
        process = self._processes.get(run_id)
        if process is None:
            return
        return_code = process.poll()
        if return_code is None:
            return
        self._processes.pop(run_id, None)
        self._close_log(run_id)
        status = "passed" if return_code == 0 else "failed"
        current = self.store.read_status(run_id) or {}
        if current.get("status") not in TERMINAL_STATUSES:
            self.store.finish_run(
                run_id,
                status,
                exit_code=return_code,
                summary=f"pytest exited with code {return_code}",
            )

    def _close_log(self, run_id: str) -> None:
        handle = self._log_files.pop(run_id, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    @staticmethod
    def _can_create(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False