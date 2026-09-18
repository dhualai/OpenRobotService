"""Schemathesis contract-testing runner."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def schemathesis_available() -> bool:
    """Return whether the Schemathesis package is importable."""
    return importlib.util.find_spec("schemathesis") is not None


def _schemathesis_command() -> List[str]:
    executable = shutil.which("schemathesis")
    if executable:
        return [executable]
    return [sys.executable, "-m", "schemathesis.cli"]


def build_contract_command(
    schema: str,
    base_url: str,
    *,
    phases: str = "examples,coverage",
    mode: str = "positive",
    max_examples: int = 5,
    checks: str = "not_a_server_error,status_code_conformance,content_type_conformance,response_schema_conformance",
    include_path_regex: Optional[str] = "^/api/(health|auth/login)$",
    report_dir: Optional[Path] = None,
    junit_path: Optional[Path] = None,
    extra_args: Optional[Sequence[str]] = None,
) -> List[str]:
    """Build a Schemathesis 4.x CLI command."""
    command = [
        *_schemathesis_command(),
        "run",
        schema,
        "--url",
        base_url,
        "--checks",
        checks,
        "--phases",
        phases,
        "--mode",
        mode,
        "--max-examples",
        str(max_examples),
        "--output-sanitize",
        "true",
    ]
    if include_path_regex:
        command.extend(["--include-path-regex", include_path_regex])
    if report_dir:
        command.extend(["--report", "junit,allure", "--report-dir", str(report_dir)])
    if junit_path:
        command.extend(["--report-junit-path", str(junit_path)])
    command.extend(extra_args or [])
    return command


def run_contract(
    schema: str,
    base_url: str,
    *,
    cwd: Optional[Path] = None,
    timeout: float = 300.0,
    **kwargs,
) -> Dict[str, object]:
    """Run Schemathesis and return a structured result."""
    if not schemathesis_available():
        return {
            "available": False,
            "passed": False,
            "command": [],
            "returncode": None,
            "stdout": "",
            "stderr": "schemathesis is not installed",
        }
    command = build_contract_command(schema, base_url, **kwargs)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "available": True,
        "passed": completed.returncode == 0,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
    }