"""Login + Call UI smoke.

This test is opt-in because it needs a running backend and a real test user:

    PLAYWRIGHT_E2E_ENABLED=1
    PLAYWRIGHT_BASE_URL=http://127.0.0.1:5173
    PLAYWRIGHT_USERNAME=u1_auto
    PLAYWRIGHT_PASSWORD=123456

It verifies the user can log in and reach the Call workspace with the chat
input visible. The full AI/ticket flow remains a later E2E step.
"""

from __future__ import annotations

import os

import allure
import pytest

pytestmark = [pytest.mark.ui, pytest.mark.e2e]

_BASE_URL = os.getenv("PLAYWRIGHT_BASE_URL", "").rstrip("/")
_ENABLED = os.getenv("PLAYWRIGHT_E2E_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
_USERNAME = os.getenv("PLAYWRIGHT_USERNAME") or os.getenv("REAL_U1_USERNAME", "")
_PASSWORD = os.getenv("PLAYWRIGHT_PASSWORD") or os.getenv("REAL_U1_PASSWORD", "")


@allure.feature("UI Smoke")
@allure.story("我要摇人")
def test_login_then_open_call_workspace():
    if not _ENABLED:
        pytest.skip("PLAYWRIGHT_E2E_ENABLED=1 is required for Call UI smoke")
    if not _BASE_URL or not _USERNAME or not _PASSWORD:
        pytest.skip("PLAYWRIGHT_BASE_URL and PLAYWRIGHT_USERNAME/PASSWORD are required")

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright browser is unavailable: {exc}")
        page = browser.new_page()
        try:
            page.goto(f"{_BASE_URL}/login", wait_until="domcontentloaded")
            page.get_by_placeholder("请输入账号").fill(_USERNAME)
            page.get_by_placeholder("请输入密码").fill(_PASSWORD)
            page.get_by_text("登录", exact=True).first.click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)

            page.goto(f"{_BASE_URL}/call", wait_until="domcontentloaded")
            chat_input = page.get_by_placeholder("发消息…")
            assert chat_input.count() > 0
            assert chat_input.first.is_visible()
        finally:
            browser.close()