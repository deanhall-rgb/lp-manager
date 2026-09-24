from __future__ import annotations

from copy import deepcopy

import pytest

from lp_manager.profit_calibration import fee_calibration_for_pool
from lp_manager.profit_engine import recommend_profit_range


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
            rows = [r for r in rows if r.get("status") == status]
        return rows
    def get_position_snapshot(self, position_id):
        return deepcopy(self.snapshots.get(position_id))


class FakeMarket:
    def __init__(self):
        self._pool = {
            "chain":"ROBINHOOD_CHAIN","protocol":"UNISWAP_V3","pool_address":"0xpool",
            "pair":"WETH/USDG","base_token":{"symbol":"WETH"},"quote_token":{"symbol":"USDG"},
            "tvl_usd":2_000_000,"volume_24h_usd":1_200_000,"fee_tier_bps":5,
            "base_token_price_usd":2700,"quote_token_price_usd":1.0,"pool_created_at":"2025-01-01T00:00:00Z",
        }
    def pool(self, chain, address):
        row=deepcopy(self._pool); row["chain"]=chain; row["pool_address"]=address; return row
    def ohlcv_days(self, chain, address, days, timeframe="hour"):
        # A gently rising market with meaningful but non-constant volume.
        n=max(180, int(days)*(24 if timeframe=="hour" else 1))
        step=3600 if timeframe=="hour" else 86400
        rows=[]
        for i in range(n):
            wave=(i%24-12)*0.7
            close=2450 + i*(260/max(n,1)) + wave
            rows.append({
                "timestamp":1_780_000_000+i*step,
                "open":close-2,"high":close+18,"low":close-18,"close":close,
                "volume":650_000 + (i%12)*25_000,
            })
        return rows


def _patch_onchain(monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain,address:{"ok":False,"error":"test fallback"})


def test_profit_engine_auto_classifies_major_stable_core(monkeypatch):
    _patch_onchain(monkeypatch)
    out = recommend_profit_range(FakeMarket(), MemoryStore(), "ROBINHOOD_CHAIN", "0xpool", horizon_days=7, capital=1000, sleeve="AUTO", monthly_target_pct=10)
    assert out["sleeve"] == "CORE_INCOME"
    assert out["horizon_days"] == 7
    assert out["recommended_range"]["lower"] < out["spot"] < out["recommended_range"]["upper"]
    assert out["recommended_range"]["forecast"]["expected_fees_usd"] > 0
    assert out["recommended_range"]["walk_forward"]["holdout"]["windows"] > 0


def test_profit_engine_horizon_changes_search_geometry(monkeypatch):
    _patch_onchain(monkeypatch)
    market=FakeMarket(); store=MemoryStore()
    short = recommend_profit_range(market, store, "ROBINHOOD_CHAIN", "0xpool", horizon_days=3, capital=1000, sleeve="TACTICAL_CAMPAIGN", monthly_target_pct=10)
    long = recommend_profit_range(market, store, "ROBINHOOD_CHAIN", "0xpool", horizon_days=30, capital=1000, sleeve="CORE_INCOME", monthly_target_pct=10)
    assert short["recommended_range"]["forecast"]["horizon_days"] == 3
    assert long["recommended_range"]["forecast"]["horizon_days"] == 30
    assert short["recommended_range"]["width_pct"] != long["recommended_range"]["width_pct"] or short["sleeve"] != long["sleeve"]


def test_exact_pool_live_fee_sample_calibrates_forecast(monkeypatch):
    store=MemoryStore()
    pool=FakeMarket().pool("ROBINHOOD_CHAIN","0xpool")
    store.positions=[{
        "id":"live:1","status":"OPEN","pair":"WETH/USDG","chain":"ROBINHOOD_CHAIN","pool_address":"0xpool",
        "strategy_sleeve":"CORE_INCOME","capital_value":1000,"current_value":1000,
        "lower_price":2400,"upper_price":3000,"current_price":2700,
    }]
    store.snapshots["live:1"]={"market":pool,"pool_address":"0xpool"}
    # Mature exact-pool evidence deliberately above the model estimate.
    store.settings["fees:tracker:live:1"]={"age_days":2.0,"fees_24h_usd":8.0,"cumulative_earned_usd":16.0}
    cal=fee_calibration_for_pool(store,pool,sleeve="CORE_INCOME")
    assert cal["sample_count"] == 1
    assert cal["exact_pool_samples"] == 1
    assert cal["factor"] > 1.0
    assert cal["confidence"] in {"MODERATE","HIGH"}


def test_holdout_is_present_as_validation_evidence(monkeypatch):
    _patch_onchain(monkeypatch)
    out=recommend_profit_range(FakeMarket(),MemoryStore(),"ROBINHOOD_CHAIN","0xpool",horizon_days=7,capital=500,sleeve="CORE_INCOME")
    wf=out["recommended_range"]["walk_forward"]
    assert wf["train"]["windows"] > 0
    assert wf["holdout"]["windows"] > 0
    assert "net_p25_usd" in wf["holdout"]
    assert out["objective"].startswith("MAXIMISE_EXPECTED_NET_LP_FEE_PROFIT")
