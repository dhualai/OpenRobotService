"""Unit tests for page-action helpers."""

from __future__ import annotations

from datetime import date, datetime

from automation.src.ui_regression.page_actions import _calendar_month_direction, default_end_time


def test_default_end_time_is_future_and_formatted():
    value = default_end_time(days=3)
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")

    assert parsed > datetime.now()


def test_date_picker_moves_from_displayed_month():
    shown = ["2026-11-30", "2026-12-01", "2027-01-03"]
    assert _calendar_month_direction(date(2026, 11, 27), shown) == "prev"
    assert _calendar_month_direction(date(2027, 1, 10), shown) == "next"
