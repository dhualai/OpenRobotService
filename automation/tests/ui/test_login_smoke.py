"""Playwright UI smoke tests.

The first smoke only verifies that the Vite app loads and the login form is
visible. It uses existing placeholder/text locators as an interim measure.
The stable locator contract is documented in design-ui-automation-selectors.md.
"""

from __future__ import annotations

import os

import allure
import pytest

pytestmark = [pytest.mark.ui, pytest.mark.smoke]

_BASE_URL = os.getenv("PLAYWRIGHT_BASE_URL", "").rstrip("/")
_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1").strip().lower() not in {"0", "false", "no", "off"}


@pytest.fixture
def login_page():
    if not _BASE_URL:
        pytest.skip("PLAYWRIGHT_BASE_URL is required for UI smoke tests")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=_HEADLESS)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright browser is unavailable: {exc}")
        page = browser.new_page()
        page.goto(f"{_BASE_URL}/login", wait_until="domcontentloaded")
        try:
            yield page
        finally:
            browser.close()


@allure.feature("UI Smoke")
@allure.story("登录页")
def test_login_page_loads(login_page):
    assert "/login" in login_page.url
    assert login_page.title() is not None


@allure.feature("UI Smoke")
@allure.story("登录页")
def test_login_form_controls_visible(login_page):
    username = login_page.get_by_placeholder("请输入账号")
    password = login_page.get_by_placeholder("请输入密码")
    submit = login_page.get_by_text("登录", exact=True)

    assert username.count() > 0 and username.first.is_visible()
    assert password.count() > 0 and password.first.is_visible()
    assert submit.count() > 0 and submit.first.is_visible()