"""UI test fixtures: Playwright browser and page objects."""

import pytest
from playwright.sync_api import sync_playwright

FRONTEND_URL = "http://localhost:5173"


@pytest.fixture(scope="session")
def browser():
    """Session-scoped Chromium browser."""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """Function-scoped Playwright page. Skip if frontend unavailable."""
    context = browser.new_context()
    p = context.new_page()
    try:
        p.goto(FRONTEND_URL, timeout=5000, wait_until="domcontentloaded")
    except Exception as e:
        pytest.skip(f"Frontend not available at {FRONTEND_URL}: {e}")
    yield p
    context.close()
