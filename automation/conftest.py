"""Auto-open Allure report after a test run.

When pytest is invoked with --alluredir, the session-finish hook generates
the HTML report, starts a local HTTP server (if not already running) and
opens the browser automatically.

Disabled in CI environments (CI / GITHUB_ACTIONS) or when
ALLURE_AUTO_OPEN=0 is set.
"""

import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

_REPORT_PORT = 8080
_ALREADY_RAN = False


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
    allure_exe = shutil.which("allure") or shutil.which("allure.bat")
    if not allure_exe:
        print("  [allure] CLI not found, skip report generation")
        return

    # Keep local history across runs (trend charts / history tab)
    history_src = report_dir / "history"
    if history_src.is_dir():
        shutil.copytree(history_src, results_dir / "history", dirs_exist_ok=True)

    gen = subprocess.run(
        [allure_exe, "generate", str(results_dir), "-o", str(report_dir), "--clean"],
        capture_output=True, text=True,
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
            [sys.executable, "-m", "http.server", str(_REPORT_PORT),
             "--directory", str(report_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **kwargs,
        )
    webbrowser.open(f"http://localhost:{_REPORT_PORT}")


def pytest_configure(config):
    alluredir = config.getoption("--alluredir", None)
    if alluredir:
        try:
            from automation.src.reporting.metadata import write_allure_metadata
            write_allure_metadata(Path(alluredir))
        except Exception as e:
            print(f"  [allure] metadata skipped: {e}")


def pytest_sessionfinish(session, exitstatus):
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
