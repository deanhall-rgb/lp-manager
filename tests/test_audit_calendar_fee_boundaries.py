from __future__ import annotations

from datetime import datetime

from lp_manager.fee_metrics import calendar_fee_metrics, calendar_fee_window


def test_short_interval_crossing_boundary_is_unallocated():
    start = 2_000_000_000.0
    tracker = {"observations": [
        {"timestamp": start - 300, "cumulative_earned_usd": 10.0},
        {"timestamp": start + 300, "cumulative_earned_usd": 15.0},
        {"timestamp": start + 900, "cumulative_earned_usd": 17.0},
    ]}
    out = calendar_fee_window(tracker, start, start + 1000, now_ts=start + 1000)
    assert out["actual_usd"] == 2.0
    assert out["unallocated_boundary_usd"] == 5.0
    assert out["unallocated_gap_usd"] == 0.0
    assert out["boundary_count"] == 1
    assert out["observed_seconds"] == 600.0
    assert out["coverage_start"] == start + 300
    assert out["quality"].startswith("PARTIAL")


def test_observation_exactly_on_boundary_is_valid_start_of_window():
    start = 2_000_000_000.0
    tracker = {"observations": [
        {"timestamp": start - 60, "cumulative_earned_usd": 10.0},
        {"timestamp": start, "cumulative_earned_usd": 11.0},
        {"timestamp": start + 600, "cumulative_earned_usd": 14.0},
    ]}
    out = calendar_fee_window(tracker, start, start + 700, now_ts=start + 700)
    assert out["actual_usd"] == 3.0
    assert out["unallocated_boundary_usd"] == 1.0
    assert out["observed_seconds"] == 600.0


def test_inside_window_without_prior_keeps_contiguous_fee_income():
    start = 2_000_000_000.0
    tracker = {"observations": [
        {"timestamp": start + 60, "cumulative_earned_usd": 5.0},
        {"timestamp": start + 120, "cumulative_earned_usd": 7.5},
    ]}
    out = calendar_fee_window(tracker, start, start + 180, now_ts=start + 180)
    assert out["actual_usd"] == 2.5
    assert out["unallocated_boundary_usd"] == 0.0
    assert out["boundary_count"] == 0


def test_prior_only_does_not_claim_complete_coverage():
    start = 2_000_000_000.0
    tracker = {"observations": [
        {"timestamp": start - 300, "cumulative_earned_usd": 5.0},
    ]}
    out = calendar_fee_window(tracker, start, start + 180, now_ts=start + 180)
    assert out["actual_usd"] == 0.0
    assert out["coverage_start"] is None
    assert out["coverage_end"] is None
    assert out["quality"].startswith("PARTIAL")


def test_calendar_today_surfaces_unallocated_boundary_amount():
    now = 2_000_000_000.0
    local = datetime.fromtimestamp(now).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    tracker = {"observations": [
        {"timestamp": start - 300, "cumulative_earned_usd": 10.0},
        {"timestamp": start + 300, "cumulative_earned_usd": 15.0},
        {"timestamp": start + 900, "cumulative_earned_usd": 17.0},
    ]}
    out = calendar_fee_metrics(tracker, now_ts=now)
    assert out["today"]["actual_usd"] == 2.0
    assert out["today"]["unallocated_boundary_usd"] == 5.0
    assert out["today"]["boundary_count"] == 1
