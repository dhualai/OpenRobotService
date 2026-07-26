"""Screenshot utilities for UI tests."""
from pathlib import Path

SCREENSHOT_DIR = Path("output/screenshots")


def take_screenshot(page, name):
    """Save a screenshot to the output directory."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    return path
