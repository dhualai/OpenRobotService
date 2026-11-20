"""Preflight the UI regression SSH forwards used by GitHub Actions."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from automation.src.ui_regression.config import UiRegressionConfig  # noqa: E402
from automation.src.ui_regression.tunnels import UiTunnelManager  # noqa: E402


HTTP_TIMEOUT_SECONDS = 30.0
HTTP_ATTEMPTS = 3
TCP_TIMEOUT_SECONDS = 5.0
RETRY_DELAY_SECONDS = 2.0


def _key_fingerprint(key_path: str) -> str:
    if not key_path:
        return "未配置私钥路径"
    expanded = str(Path(key_path).expanduser())
    public_key = subprocess.run(
        ["ssh-keygen", "-y", "-f", expanded],
        check=False,
        capture_output=True,
        text=True,
    )
    if public_key.returncode != 0:
        detail = public_key.stderr.strip() or public_key.stdout.strip()
        raise RuntimeError(f"无法读取 SSH 私钥: {detail}")
    fingerprint = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=public_key.stdout,
        check=False,
        capture_output=True,
        text=True,
    )
    if fingerprint.returncode != 0:
        detail = fingerprint.stderr.strip() or fingerprint.stdout.strip()
        raise RuntimeError(f"无法生成 SSH 公钥指纹: {detail}")
    return fingerprint.stdout.strip()


def _check_http(
    url: str,
    *,
    label: str,
    attempts: int = HTTP_ATTEMPTS,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        print(f"{label}: attempt {attempt}/{attempts}")
        try:
            response = httpx.get(
                url,
                timeout=timeout,
                trust_env=False,
            )
            response.raise_for_status()
            return
        except Exception as exc:  # noqa: BLE001 - retry keeps the final cause
            last_error = exc
            print(f"{label}: attempt {attempt} failed: {exc}")
            if attempt < attempts:
                time.sleep(retry_delay)
    raise RuntimeError(
        f"{label} failed after {attempts} attempts: {last_error}"
    ) from last_error


def _check_tcp(
    host: str,
    port: int,
    *,
    label: str,
    attempts: int = HTTP_ATTEMPTS,
    timeout: float = TCP_TIMEOUT_SECONDS,
    retry_delay: float = RETRY_DELAY_SECONDS,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        print(f"{label}: attempt {attempt}/{attempts}")
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return
        except OSError as exc:
            last_error = exc
            print(f"{label}: attempt {attempt} failed: {exc}")
            if attempt < attempts:
                time.sleep(retry_delay)
    raise RuntimeError(
        f"{label} failed after {attempts} attempts: {last_error}"
    ) from last_error


def main() -> int:
    config = UiRegressionConfig.from_env()
    manager = UiTunnelManager(config)
    try:
        print(f"SSH key fingerprint: {_key_fingerprint(config.ssh_key)}")
        backend_url, ai_url = manager.start()
        print(f"SSH forwards ready: {backend_url}, {ai_url}")

        _check_http(
            f"{backend_url}/api/health",
            label="Backend health",
        )
        print("Backend health: OK")

        _check_http(
            f"{ai_url}/health",
            label="Automation AI health",
        )
        print("Automation AI health: OK")

        if config.db_cleanup_enabled and manager.db_local_port is not None:
            _check_tcp(
                "127.0.0.1",
                manager.db_local_port,
                label="Database forward",
            )
            print(f"Database forward: OK (127.0.0.1:{manager.db_local_port})")

        print("UI regression SSH precheck passed")
        return 0
    except Exception as exc:  # noqa: BLE001 - CI preflight reports the exact stage
        print(f"UI regression SSH precheck failed: {exc}", file=sys.stderr)
        return 1
    finally:
        manager.stop()


if __name__ == "__main__":
    raise SystemExit(main())
