"""UI test fixtures: Playwright browser and page objects."""

import pytest
import httpx
from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"


@pytest.fixture(scope="session")
def browser():
    """Session-scoped Chromium browser. Skip if Playwright not installed."""
    pytest.importorskip("playwright")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """Function-scoped page with frontend availability check."""
    try:
        httpx.get(FRONTEND_URL, timeout=3)
    except Exception:
        pytest.skip(f"Frontend not available at {FRONTEND_URL}")

    context = browser.new_context()
    p = context.new_page()
    try:
        p.goto(FRONTEND_URL, timeout=5000, wait_until="domcontentloaded")
    except Exception as e:
        p.close()
        context.close()
        pytest.skip(f"Frontend unreachable: {e}")

    yield p
    p.close()
    context.close()
