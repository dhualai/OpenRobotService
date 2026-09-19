"""Fixtures for the opt-in real-environment UI regression scenario."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterator

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from automation.src.remote import SSHTunnel, SSHTunnelConfig, free_port
from automation.src.ui_regression.config import UiRegressionConfig
from automation.src.ui_regression.db_cleanup import DatabaseCleanupConfig
from automation.src.ui_regression.gateway import create_app
from automation.src.ui_regression.tunnels import UiTunnelManager


@pytest.fixture(scope="session", autouse=True)
def silence_http_client_info_logs() -> Iterator[None]:
    """Prevent proxied URLs with credentials from entering pytest log attachments."""

    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    previous_levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.setLevel(level)


@dataclass
class UiRegressionRuntime:
    gateway_url: str
    backend_url: str
    ai_url: str
    browser: Browser
    u1_context: BrowserContext
    u2_context: BrowserContext
    u1_page: Page
    u2_page: Page
    u1_username: str
    u1_password: str
    u2_username: str
    u2_password: str
    cleanup_username: str
    cleanup_password: str
    db_cleanup_config: DatabaseCleanupConfig | None


def _wait_for_gateway(url: str, server: uvicorn.Server, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if not server.started:
            time.sleep(0.1)
            continue
        try:
            response = httpx.get(f"{url}/_gateway/health", timeout=2.0)
            if response.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - startup polling
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"UI regression Gateway did not start: {last_error}")


@pytest.fixture(scope="session")
def ui_regression_runtime() -> Iterator[UiRegressionRuntime]:
    if os.getenv("UI_REGRESSION_E2E", "0") != "1":
        pytest.skip("UI_REGRESSION_E2E=1 is required")

    config = UiRegressionConfig.from_env()
    if not config.frontend_dist.joinpath("index.html").is_file():
        pytest.fail(f"frontend dist is missing: {config.frontend_dist}")
    if not config.ssh_host or not config.ssh_user:
        pytest.fail("UI_REGRESSION_SSH_HOST and UI_REGRESSION_SSH_USER are required")

    u1_username = os.getenv("UI_REGRESSION_U1_USERNAME", "")
    u1_password = os.getenv("UI_REGRESSION_U1_PASSWORD", "")
    u2_username = os.getenv("UI_REGRESSION_U2_USERNAME", "")
    u2_password = os.getenv("UI_REGRESSION_U2_PASSWORD", "")
    if not all((u1_username, u1_password, u2_username, u2_password)):
        pytest.fail("U1/U2 UI regression credentials are required")

    tunnel_manager = UiTunnelManager(config)
    backend_url, ai_url = tunnel_manager.start()

    db_tunnel: SSHTunnel | None = None
    db_cleanup_config: DatabaseCleanupConfig | None = None
    if os.getenv("UI_REGRESSION_DB_CLEANUP_ENABLED", "0") == "1":
        try:
            db_tunnel = SSHTunnel(
                SSHTunnelConfig(
                    host=config.ssh_host,
                    user=config.ssh_user,
                    ssh_port=config.ssh_port,
                    key_path=config.ssh_key,
                    remote_host="127.0.0.1",
                    remote_port=int(
                        os.getenv("UI_REGRESSION_DB_REMOTE_PORT", "3306")
                    ),
                    local_host="127.0.0.1",
                    local_port=int(
                        os.getenv("UI_REGRESSION_DB_LOCAL_PORT", "19402")
                    ),
                    connect_timeout=config.tunnel_timeout,
                )
            )
            db_tunnel.start()
            db_cleanup_config = DatabaseCleanupConfig.from_env(
                port_override=db_tunnel.local_port
            )
        except Exception:
            tunnel_manager.stop()
            raise

    gateway_port = free_port()
    gateway_config = UiRegressionConfig(
        host="127.0.0.1",
        port=gateway_port,
        frontend_dist=config.frontend_dist,
        backend_url=backend_url,
        ai_url=ai_url,
    )
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(gateway_config),
            host="127.0.0.1",
            port=gateway_port,
            log_level="warning",
        )
    )
    server_thread = threading.Thread(
        target=server.run,
        name="ui-regression-gateway",
        daemon=True,
    )
    server_thread.start()
    _wait_for_gateway(gateway_url, server)

    playwright = sync_playwright().start()
    headless = os.getenv("UI_REGRESSION_HEADLESS", "1") not in {
        "0",
        "false",
        "no",
        "off",
    }
    browser = playwright.chromium.launch(headless=headless)
    context_options = {
        "viewport": {"width": 430, "height": 932},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
    }
    u1_context = browser.new_context(**context_options)
    u2_context = browser.new_context(**context_options)
    u1_page = u1_context.new_page()
    u2_page = u2_context.new_page()

    try:
        yield UiRegressionRuntime(
            gateway_url=gateway_url,
            backend_url=backend_url,
            ai_url=ai_url,
            browser=browser,
            u1_context=u1_context,
            u2_context=u2_context,
            u1_page=u1_page,
            u2_page=u2_page,
            u1_username=u1_username,
            u1_password=u1_password,
            u2_username=u2_username,
            u2_password=u2_password,
            cleanup_username=os.getenv("UI_REGRESSION_CLEANUP_USERNAME", ""),
            cleanup_password=os.getenv("UI_REGRESSION_CLEANUP_PASSWORD", ""),
            db_cleanup_config=db_cleanup_config,
        )
    finally:
        u1_context.close()
        u2_context.close()
        browser.close()
        playwright.stop()
        server.should_exit = True
        server_thread.join(timeout=10)
        if db_tunnel is not None:
            db_tunnel.stop()
        tunnel_manager.stop()
