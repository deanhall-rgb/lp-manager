"""Regression tests: a measured zero rolling fee rate must not become lifetime APR."""

import math

import pytest

from lp_manager.profit_calibration import (
    _observed_fee_day,
    calibration_samples,
    observed_pair_fee_prior,
    observed_pool_fee_rate,
)


class _Store:
    def __init__(self, tracker):
        self.tracker = tracker
        self.position = {
            "id": "position-1",
            "status": "OPEN",
            "pool_address": "0xabc",
            "chain": "BASE",
            "pair": "WETH/USDC",
            "capital_value": 1000.0,
            "current_value": 1000.0,
            "lower_price": 2000.0,
            "upper_price": 4000.0,
            "current_price": 3000.0,
        }
        self.snapshot = {
            "pool_address": "0xabc",
            "market": {
                "pool_address": "0xabc",
                "chain": "BASE",
                "pair": "WETH/USDC",
                "fee_tier_bps": 5.0,
                "tvl_usd": 1000000.0,
                "volume_24h_usd": 100000.0,
            },
        }

    def list_positions(self, status=None):
        if status and status != "OPEN":
            return []
        return [dict(self.position)]

    def get_position_snapshot(self, position_id):
        return dict(self.snapshot) if position_id == "position-1" else None

    def get_setting(self, key, default=None):
        if key == "fees:tracker:position-1":
            return dict(self.tracker)
        return default


def test_measured_zero_24h_fees_do_not_fall_back_to_lifetime_average():
    tracker = {"age_days": 10.0, "fees_24h_usd": 0.0, "cumulative_earned_usd": 100.0}
    assert _observed_fee_day(tracker) == (0.0, "ROLLING_24H")


def test_measured_zero_is_not_used_as_positive_pool_or_pair_prior():
    tracker = {"age_days": 10.0, "fees_24h_usd": 0.0, "cumulative_earned_usd": 100.0}
    store = _Store(tracker)
    pool = {"pool_address": "0xabc", "chain": "BASE", "pair": "WETH/USDC", "fee_tier_bps": 5.0}
    assert observed_pool_fee_rate(store, pool)["available"] is False
    assert observed_pair_fee_prior(store, pool)["available"] is False
    assert calibration_samples(store) == []


@pytest.mark.parametrize("missing", [None, "not-a-number", float("nan"), float("inf")])
def test_missing_or_invalid_rolling_measurement_retains_labelled_historical_fallback(missing):
    tracker = {"age_days": 10.0, "cumulative_earned_usd": 100.0}
    if missing is not None:
        tracker["fees_24h_usd"] = missing
    assert _observed_fee_day(tracker) == (10.0, "SINCE_OPEN_DAILY_AVERAGE")


def test_positive_rolling_fees_and_young_position_policy_unchanged():
    assert _observed_fee_day({
        "age_days": 10.0, "fees_24h_usd": 2.5, "cumulative_earned_usd": 100.0
    }) == (2.5, "ROLLING_24H")
    assert _observed_fee_day({
        "age_days": 0.5, "fees_24h_usd": 2.5, "cumulative_earned_usd": 100.0
    }) == (0.0, "INSUFFICIENT_UNDER_24H")
