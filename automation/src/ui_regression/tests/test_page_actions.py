"""Unit tests for page-action helpers."""

from __future__ import annotations

from datetime import datetime

from automation.src.ui_regression.page_actions import default_end_time


def test_default_end_time_is_future_and_formatted():
    value = default_end_time(days=3)
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")

    assert parsed > datetime.now()
