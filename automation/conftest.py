"""OpenRobot pytest entry hooks.

Two responsibilities live here:
1. Record a runtime trace for every pytest session.
2. Optionally generate and open the Allure report after a local run.
"""

import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

import pytest

from automation.src.runtime import RunContext, TraceStore

_REPORT_PORT = 8080
_ALREADY_RAN = False


def pytest_addoption(parser):
    group = parser.getgroup("openrobot-runtime")
    group.addoption(
        "--run-scenario",
        default=os.getenv("OPENROBOT_SCENARIO", "pytest"),
        help="Scenario name recorded in the runtime run metadata.",
    )
    group.addoption(
        "--run-profile",
        default=os.getenv("OPENROBOT_PROFILE", "fast"),
        help="Runtime profile: fast, pro, or nightly.",
    )
    group.addoption(
        "--run-env",
        default=os.getenv("OPENROBOT_ENV", os.getenv("AUTOMATION_ENV", "local")),
        help="Target environment recorded in the runtime run metadata.",
    )
    group.addoption(
        "--no-trace",
        action="store_true",
        default=False,
        help="Disable the OpenRobot runtime trace for this pytest session.",
    )


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _open_allure_report(alluredir: str) -> None:
    results_dir = Path(alluredir).resolve()
    if not results_dir.is_dir() or not list(results_dir.glob("*-result.json")):
        print("  [allure] no results found, skip report generation")
        return

    report_dir = results_dir.parent / "allure-report"
    # npm installs Allure as allure.cmd on Windows. A bare "allure" resolves
    # to allure.ps1 in PowerShell, which shutil.which does not execute.
    allure_exe = (
        shutil.which("allure.cmd")
        or shutil.which("allure.bat")
        or shutil.which("allure")
    )
    if not allure_exe:
        print("  [allure] CLI not found, skip report generation")
        return

    # Keep local history across runs (trend charts / history tab)
    history_src = report_dir / "history"
    if history_src.is_dir():
        shutil.copytree(history_src, results_dir / "history", dirs_exist_ok=True)

    gen = subprocess.run(
        [allure_exe, "generate", str(results_dir), "-o", str(report_dir), "--clean"],
        capture_output=True,
        text=True,
    )
    if gen.returncode != 0:
        print(f"  [allure] generate failed: {gen.stderr.strip() or gen.stdout.strip()}")
        return
    print(f"  [allure] report generated: {report_dir}")

    if not _port_in_use(_REPORT_PORT):
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(_REPORT_PORT),
                "--directory",
                str(report_dir),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
    webbrowser.open(f"http://localhost:{_REPORT_PORT}")


def pytest_configure(config):
    alluredir = config.getoption("--alluredir", None)
    if alluredir:
        try:
            from automation.src.reporting.metadata import write_allure_metadata

            write_allure_metadata(Path(alluredir))
        except Exception as exc:  # noqa: BLE001 - reporting must not break tests
            print(f"  [allure] metadata skipped: {exc}")

    if config.getoption("--no-trace"):
        return
    try:
        context = RunContext.create(
            scenario=config.getoption("--run-scenario"),
            profile=config.getoption("--run-profile"),
            env=config.getoption("--run-env"),
            metadata={"command": " ".join(sys.argv)},
            run_id=os.getenv("OPENROBOT_RUN_ID"),
            trace_id=os.getenv("OPENROBOT_TRACE_ID"),
        )
        store = TraceStore()
        store.start_run(context)
        config._openrobot_run_context = context
        config._openrobot_trace_store = store
    except Exception as exc:  # noqa: BLE001 - tracing must not break test execution
        print(f"  [runtime] trace disabled: {exc}")


def pytest_sessionfinish(session, exitstatus):
    store = getattr(session.config, "_openrobot_trace_store", None)
    context = getattr(session.config, "_openrobot_run_context", None)
    if store is not None and context is not None:
        status = "passed" if exitstatus == 0 else "failed"
        store.finish_run(
            context.run_id,
            status,
            exit_code=exitstatus,
            summary=f"pytest exit status: {exitstatus}",
        )

    global _ALREADY_RAN
    if _ALREADY_RAN:
        return
    alluredir = session.config.getoption("--alluredir", None)
    if not alluredir:
        return
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        return
    if os.getenv("ALLURE_AUTO_OPEN", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    _ALREADY_RAN = True
    print("\n[allure] auto-open enabled, generating report...")
    _open_allure_report(alluredir)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Record setup and call outcomes as structured runtime steps."""
    outcome = yield
    report = outcome.get_result()
    if call.when not in ("setup", "call"):
        return
    store = getattr(item.config, "_openrobot_trace_store", None)
    context = getattr(item.config, "_openrobot_run_context", None)
    if store is None or context is None:
        return
    step = {
        "step": "test",
        "phase": call.when,
        "nodeid": item.nodeid,
        "status": report.outcome,
        "duration_ms": round(report.duration * 1000, 2),
    }
    if report.failed:
        step["error"] = str(report.longrepr)[-2000:]
    try:
        store.record_step(context.run_id, step)
    except Exception:
        pass


@pytest.fixture(scope="session")
def run_context(request):
    """Return the current runtime run context, if tracing is enabled."""
    return getattr(request.config, "_openrobot_run_context", None)


@pytest.fixture(scope="session")
def trace_store(request):
    """Return the current trace store, if tracing is enabled."""
    return getattr(request.config, "_openrobot_trace_store", None)