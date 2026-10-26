"""Regression: local daily accounting windows must honour historical DST."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from lp_manager.performance_log import _local_day_window


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="Requires POSIX TZ override")
@pytest.mark.parametrize(
    ("day_key", "expected_hours"),
    [
        ("2026-03-29", 23.0),  # UK clocks move forward.
        ("2026-10-25", 25.0),  # UK clocks move back.
        ("2026-10-26", 24.0),  # Ordinary day after transition.
    ],
)
def test_local_day_window_uses_offset_at_each_historical_midnight(
    monkeypatch, day_key, expected_hours
):
    with monkeypatch.context() as patch:
        patch.setenv("TZ", "Europe/London")
        time.tzset()
        try:
            day = datetime.fromisoformat(day_key).date()
            zone = ZoneInfo("Europe/London")
            expected_start = datetime.combine(day, datetime.min.time(), zone).timestamp()
            expected_end = datetime.combine(
                day + timedelta(days=1), datetime.min.time(), zone
            ).timestamp()

            start, end = _local_day_window(day_key)
            assert start == pytest.approx(expected_start)
            assert end == pytest.approx(expected_end)
            assert (end - start) / 3600.0 == pytest.approx(expected_hours)
        finally:
            # Restore the process timezone for unrelated tests.
            time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="Requires POSIX TZ override")
def test_adjacent_local_day_windows_are_contiguous_across_clock_change(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setenv("TZ", "Europe/London")
        time.tzset()
        try:
            _, sunday_end = _local_day_window("2026-10-25")
            monday_start, _ = _local_day_window("2026-10-26")
            assert sunday_end == monday_start
        finally:
            time.tzset()
