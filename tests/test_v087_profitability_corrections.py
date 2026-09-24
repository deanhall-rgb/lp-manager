from __future__ import annotations

from copy import deepcopy

import pytest

from lp_manager.fee_metrics import forecast_fee_metrics, observed_fee_metrics
from lp_manager.portfolio_accounting import position_accounting
from lp_manager.price_units import display_lens, validate_display_lens
from lp_manager.profit_calibration import fee_calibration_for_pool
from lp_manager.profit_engine import (
    _boundary_inventory_outcomes,
    _diverse_alternatives,
    _load_pool_and_history,
    recommend_profit_range,
)
from lp_manager.strategy_lab import analyse_live_pool


class MemoryStore:
    def __init__(self):
        self.settings = {}
        self.positions = []
        self.snapshots = {}

    def get_setting(self, key, default=None):
        return deepcopy(self.settings.get(key, default))

    def set_setting(self, key, value):
        self.settings[key] = deepcopy(value)

    def list_positions(self, status=None):
        rows = deepcopy(self.positions)
        if status:
            rows = [x for x in rows if str(x.get("status")) == str(status)]
        return rows

    def get_position_snapshot(self, position_id):
        return deepcopy(self.snapshots.get(position_id, {}))


def test_observed_apr_is_suppressed_before_24h():
    m = observed_fee_metrics(
        {"age_days": 0.5, "fees_24h_usd": 4.0, "cumulative_earned_usd": 4.0},
        1000.0,
    )
    assert m["annualisation_suppressed"] is True
    assert m["spot_1d_annualised_fee_apr_pct"] is None
    assert m["since_open_annualised_fee_apr_pct"] is None
    assert m["confidence"] == "INSUFFICIENT"


def test_forecast_cash_identity_never_makes_net_exceed_fee_income_without_named_return():
    m = forecast_fee_metrics(
        capital_usd=1000.0,
        horizon_days=7.0,
        expected_fees_usd=23.65,
        expected_cash_costs_usd=2.75,
    )
    assert m["expected_net_usd"] == pytest.approx(20.90, abs=0.01)
    assert m["expected_net_usd"] <= m["expected_fees_usd"]
    assert m["identity"] == "NET = FORECAST_FEES + EXPLICIT_OTHER_RETURN - CASH_COSTS"


def test_weth_stable_execution_lens_rejects_one_dollar_as_stable_per_eth():
    bad = {
        "current": 0.998996,
        "lower": 0.91,
        "upper": 1.08,
        "unit": "USDG_PER_WETH",
        "token0_symbol": "WETH",
        "token1_symbol": "USDG",
    }
    checked = validate_display_lens(bad)
    assert checked["valid"] is False
    assert "IMPLAUSIBLE_STABLE_PER_ETH_PRICE" in checked["reasons"]

    good = display_lens("WETH", "USDG", 2449.92, 2924.86, 2625.0)
    assert good["unit"] == "USDG_PER_WETH"
    assert good["validation"]["valid"] is True


def test_young_exact_pool_calibration_is_shrunk_and_capped(monkeypatch):
    store = MemoryStore()
    pool = {
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDG"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 1_000_000,
        "fee_tier_bps": 5.0,
    }
    store.positions = [{
        "id": "p4",
        "status": "OPEN",
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "strategy_sleeve": "CORE_INCOME",
        "capital_value": 1000,
        "current_value": 1000,
        "lower_price": 2400,
        "upper_price": 3000,
        "current_price": 2650,
    }]
    store.snapshots["p4"] = {"market": pool, "pool_address": "0xpool"}
    store.settings["fees:tracker:p4"] = {
        "age_days": 1.05,
        "fees_24h_usd": 1000.0,
        "cumulative_earned_usd": 1050.0,
    }
    cal = fee_calibration_for_pool(store, pool, sleeve="CORE_INCOME")
    assert cal["exact_pool_samples"] == 1
    assert cal["factor"] <= 1.25
    assert cal["factor"] >= 0.80
    assert cal["confidence"] == "MODERATE"


def test_sub_24h_sample_is_excluded_from_calibration():
    store = MemoryStore()
    pool = {
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDG"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 500_000,
        "fee_tier_bps": 5.0,
    }
    store.positions = [{
        "id": "p4", "status": "OPEN", "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool", "pair": "WETH/USDG",
        "strategy_sleeve": "CORE_INCOME", "capital_value": 1000,
        "current_value": 1000, "lower_price": 2400, "upper_price": 3000,
        "current_price": 2650,
    }]
    store.snapshots["p4"] = {"market": pool}
    store.settings["fees:tracker:p4"] = {
        "age_days": 0.5, "fees_24h_usd": 20.0, "cumulative_earned_usd": 20.0
    }
    cal = fee_calibration_for_pool(store, pool, sleeve="CORE_INCOME")
    assert cal["sample_count"] == 0
    assert cal["factor"] == 1.0
    assert cal["confidence"] == "UNAVAILABLE"


def test_position_accounting_separates_absolute_pnl_from_lp_vs_hodl():
    position = {
        "capital_value": 1000.0,
        "current_value": 980.0,
        "unclaimed_fees": 35.0,
        "realised_fees": 0.0,
        "gas_costs": 5.0,
        "cost_basis_quality": "ONCHAIN_MINT_RECONSTRUCTED",
        "opened_at": 1000.0,
    }
    snapshot = {
        "token0": {"price_usd": 2500.0},
        "token1": {"price_usd": 1.0},
        "entry_evidence": {
            "opened_at": 1000.0,
            "token0_amount": 0.2,
            "token1_amount": 500.0,
            "entry_value_usd": 1000.0,
        },
    }
    tracker = {"cumulative_earned_usd": 35.0}
    a = position_accounting(position, snapshot, tracker)
    assert a["lp_plus_fees_usd"] == pytest.approx(1010.0)
    assert a["absolute_pnl_incl_fees_usd"] == pytest.approx(10.0)
    assert a["hodl_value_usd"] == pytest.approx(1000.0)
    assert a["lp_vs_hodl_usd"] == pytest.approx(10.0)


def test_core_weth_stable_boundary_outcomes_are_asymmetric():
    pool = {
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDC"},
    }
    regime = {"direction": "BULLISH", "score": 62}
    x = _boundary_inventory_outcomes(pool, regime, "CORE_INCOME", 2400, 3100, 2700)
    assert x["below"]["asset"] == "WETH"
    assert x["above"]["asset"] == "USDC"
    assert x["below"]["utility_score"] > x["above"]["utility_score"]
    assert x["distance_below_current_pct"] > 0
    assert x["distance_above_current_pct"] > 0


def test_alternatives_are_materially_diverse():
    def row(width, score, skew=0):
        return {
            "lower": 100 - width / 2,
            "upper": 100 + width / 2,
            "width_pct": width,
            "profit_score": score,
            "skew_pct": skew,
        }
    best = row(20, 95)
    rows = [best, row(10, 85), row(18, 92), row(30, 80), row(22, 88, 9), row(25, 82)]
    alts = _diverse_alternatives(rows, best, 5)
    styles = {x["alternative_style"] for x in alts}
    assert "TIGHTER_AGGRESSIVE" in styles
    assert "WIDER_DURABLE" in styles
    assert "DIRECTIONAL" in styles


class OrientationMarket:
    def __init__(self):
        self.tokens = []

    def pool(self, chain, address):
        # Provider orientation is deliberately USDG/WETH.
        return {
            "chain": chain,
            "protocol": "UNISWAP_V3",
            "pool_address": address,
            "pair": "USDG/WETH",
            "base_token": {"address": "0x" + "1" * 40, "symbol": "USDG"},
            "quote_token": {"address": "0x" + "2" * 40, "symbol": "WETH"},
            "base_token_price_usd": 1.0,
            "quote_token_price_usd": 2625.0,
            "tvl_usd": 10_000_000,
            "volume_24h_usd": 5_000_000,
            "fee_tier_bps": 5.0,
        }

    def ohlcv_days(self, chain, address, days, timeframe="hour", token="base"):
        self.tokens.append(token)
        n = 72 if timeframe == "hour" else 30
        base = 2625.0 if token == "quote" else 1.0
        return [
            {"timestamp": 1_800_000_000 + i * 3600, "open": base, "high": base * 1.01,
             "low": base * 0.99, "close": base + i * 0.1, "volume": 100_000}
            for i in range(n)
        ]


def _usdgweth_onchain(address="0xpool", fee_bps=5.0):
    return {
        "ok": True,
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": address,
        "token0": {"address": "0x" + "2" * 40, "symbol": "WETH", "decimals": 18},
        "token1": {"address": "0x" + "1" * 40, "symbol": "USDG", "decimals": 18},
        "fee_tier": int(fee_bps * 100),
        "fee_tier_bps": fee_bps,
        "tick_spacing": 10,
        "current_tick": 0,
        "active_liquidity": "1000000",
        "price_lens": {
            "current": 2625.0,
            "lower": 2625.0,
            "upper": 2625.0,
            "unit": "USDG_PER_WETH",
            "unit_label": "USDG per WETH",
            "token0_symbol": "WETH",
            "token1_symbol": "USDG",
        },
    }


def test_usdg_weth_history_uses_weth_price_not_usdg_one_dollar(monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: _usdgweth_onchain(address))
    market = OrientationMarket()
    pool, onchain, candles, provider, warning = _load_pool_and_history(
        market, "ROBINHOOD_CHAIN", "0xpool", 30
    )
    assert market.tokens[0] == "quote"
    assert candles[-1]["close"] > 2000
    assert pool["pair"] == "WETH/USDG"
    assert onchain["price_lens"]["current"] == 2625.0


class SimpleMarket:
    def pool(self, chain, address):
        return {
            "chain": chain, "protocol": "UNISWAP_V3", "pool_address": address,
            "pair": "WETH/USDC",
            "base_token": {"address": "0x" + "2" * 40, "symbol": "WETH"},
            "quote_token": {"address": "0x" + "1" * 40, "symbol": "USDC"},
            "tvl_usd": 20_000_000, "volume_24h_usd": 10_000_000,
            "fee_tier_bps": 5.0, "base_token_price_usd": 2700.0,
            "quote_token_price_usd": 1.0, "pool_created_at": "2025-01-01T00:00:00Z",
        }

    def ohlcv_days(self, chain, address, days, timeframe="hour", token="base"):
        n=max(180, int(days)*(24 if timeframe=="hour" else 1))
        step=3600 if timeframe=="hour" else 86400
        return [
            {
                "timestamp": 1_780_000_000+i*step,
                "open": 2550+i*0.1,
                "high": 2580+i*0.1,
                "low": 2520+i*0.1,
                "close": 2555+i*0.1,
                "volume": 450_000+(i%12)*10_000,
            }
            for i in range(n)
        ]


def test_strategy_and_profit_lab_share_one_recommendation(monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: {"ok": False, "error": "test"})
    market=SimpleMarket()
    store=MemoryStore()
    profit=recommend_profit_range(
        market, store, "ETHEREUM", "0xpool",
        horizon_days=7, capital=1000, sleeve="CORE_INCOME",
        compare_fee_tiers=False,
    )
    strategy=analyse_live_pool(
        market, "ETHEREUM", "0xpool", sleeve="CORE_INCOME",
        days=7, capital=1000, pool_fallback=market.pool("ETHEREUM","0xpool"), store=store,
    )
    assert strategy["recommended_range"]["lower"] == pytest.approx(profit["recommended_range"]["lower"])
    assert strategy["recommended_range"]["upper"] == pytest.approx(profit["recommended_range"]["upper"])
    assert strategy["policy_replay"]["range_selection"]["source"] == "UNIFIED_PROFIT_ENGINE"
