from __future__ import annotations

import time

import pytest

from lp_manager.financial_truth import _matched_forecast_fees, portfolio_financial_truth


class _Store:
    def __init__(self, forecast, tracker):
        self.forecast = forecast
        self.tracker = tracker

    def list_positions(self):
        return [{
            "id": "position-1", "status": "OPEN", "source": "live_chain",
            "capital_value": 100.0, "current_value": 110.0,
            "unclaimed_fees": 0.0, "realised_fees": 0.0,
            "gas_costs": 0.0, "cost_basis_quality": "VERIFIED_OPENING_BASIS",
        }]

    def get_position_snapshot(self, position_id):
        return {}

    def get_setting(self, key, default=None):
        return self.tracker if key == "fees:tracker:position-1" else default

    def list_forecast_snapshots(self, limit=500):
        return [self.forecast]


def _forecast(start, *, horizon=1.0, fees=10.0):
    return {
        "id": "forecast-1", "position_id": "position-1",
        "created_at": start, "horizon_days": horizon,
        "expected_fees_usd": fees, "model_version": "test",
    }


def _tracker(start, amounts=(50.0, 52.0, 55.0)):
    return {
        "cumulative_earned_usd": 500.0,  # Lifetime must never become forecast actual.
        "observations": [
            {"timestamp": start + 60, "cumulative_earned_usd": amounts[0]},
            {"timestamp": start + 12 * 3600, "cumulative_earned_usd": amounts[1]},
            {"timestamp": start + 86400 - 60, "cumulative_earned_usd": amounts[2]},
        ],
    }


def test_forecast_audit_uses_matching_window_not_lifetime_fees():
    start = time.time() - 3 * 86400
    truth = portfolio_financial_truth(_Store(_forecast(start), _tracker(start)))
    comparison = truth["forecast_audit"]["comparisons"][0]
    assert comparison["actual_fees_usd"] == pytest.approx(5.0)
    assert comparison["forecast_fees_usd"] == pytest.approx(10.0)
    assert comparison["fee_forecast_error_usd"] == pytest.approx(-5.0)
    assert comparison["comparison_quality"] == "NEAR_MATCHED_OBSERVATION_WINDOW"
    assert comparison["observation_coverage_pct"] > 99.0


def test_forecast_audit_never_substitutes_lifetime_when_observations_missing():
    start = time.time() - 3 * 86400
    truth = portfolio_financial_truth(_Store(_forecast(start), {"cumulative_earned_usd": 500.0}))
    assert truth["forecast_audit"]["comparisons"] == []
    assert truth["forecast_audit"]["unmatched_count"] == 1
    assert truth["forecast_audit"]["unmatched"][0]["reason"] == "INSUFFICIENT_MATCHED_OBSERVATIONS"


def test_forecast_audit_preserves_measured_zero_earnings():
    start = time.time() - 3 * 86400
    truth = portfolio_financial_truth(_Store(_forecast(start), _tracker(start, (50.0, 50.0, 50.0))))
    comparison = truth["forecast_audit"]["comparisons"][0]
    assert comparison["actual_fees_usd"] == 0.0
    assert comparison["fee_forecast_error_usd"] == -10.0


def test_in_progress_horizon_cannot_be_compared_to_partial_fees():
    start = time.time() - 3600
    actual, status, coverage = _matched_forecast_fees(_tracker(start), start, 1.0, time.time())
    assert actual is None and coverage is None
    assert status == "HORIZON_IN_PROGRESS"


def test_sparse_tracker_gap_is_not_treated_as_full_horizon():
    start = time.time() - 3 * 86400
    tracker = {"observations": [
        {"timestamp": start + 60, "cumulative_earned_usd": 10},
        {"timestamp": start + 86400 - 60, "cumulative_earned_usd": 20},
    ]}
    actual, status, _ = _matched_forecast_fees(tracker, start, 1.0, time.time())
    assert actual is None and status == "OBSERVATION_GAP"


def test_fee_counter_reset_is_not_reported_as_negative_earnings():
    start = time.time() - 3 * 86400
    actual, status, _ = _matched_forecast_fees(_tracker(start, (50.0, 40.0, 45.0)), start, 1.0, time.time())
    assert actual is None and status == "CUMULATIVE_FEE_RESET"


def test_missing_boundary_evidence_is_not_extrapolated():
    start = time.time() - 3 * 86400
    tracker = _tracker(start)
    tracker["observations"][0]["timestamp"] = start + 7200
    actual, status, _ = _matched_forecast_fees(tracker, start, 1.0, time.time())
    assert actual is None and status == "INCOMPLETE_FORECAST_WINDOW"
