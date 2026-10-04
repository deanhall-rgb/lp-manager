from pathlib import Path
from types import SimpleNamespace
import time

from lp_manager.candidate_universe import CandidateUniverse
from lp_manager.profit_engine import (
    _candidate_skews,
    _select_regime_aware_best,
    _volatility_edge_limits,
)


class FakeStore:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def list_positions(self, status=None):
        return []


def _row(pair="QNT/WETH", address=None):
    address = address or ("0x" + "a" * 40)
    a, b = pair.split("/")
    return {
        "chain": "ETHEREUM",
        "pool_address": address,
        "pair": pair,
        "base_token": {"symbol": a},
        "quote_token": {"symbol": b},
        "economic_validation": "LIVE_VALIDATED",
        "protocol": "UNISWAP_V3",
        "tvl_usd": 500_000,
        "volume_24h_usd": 900_000,
    }


def test_tactical_skew_search_has_fine_directional_choices():
    vals = _candidate_skews(
        {
            "range_skew_pct": -3.4,
            "confidence": 72,
            "breakout_state": "DOWNSIDE_BREAKDOWN",
        },
        "TACTICAL_CAMPAIGN",
    )
    assert 0.0 in vals
    assert any(0 < abs(x) < 1.5 for x in vals)
    assert any(x < 0 for x in vals)
    assert any(x > 0 for x in vals)
    assert min(vals) >= -6.0
    assert max(vals) <= 6.0


def test_directional_regime_gets_more_asymmetry_room_but_remains_bounded():
    neutral = _volatility_edge_limits(
        "TACTICAL_CAMPAIGN",
        3,
        {
            "realised_volatility_pct": 2.0,
            "range_skew_pct": 0,
            "confidence": 60,
            "breakout_state": "INSIDE_PRIOR_RANGE",
        },
    )
    directional = _volatility_edge_limits(
        "TACTICAL_CAMPAIGN",
        3,
        {
            "realised_volatility_pct": 2.0,
            "range_skew_pct": -6.0,
            "confidence": 80,
            "breakout_state": "DOWNSIDE_BREAKDOWN",
        },
    )
    assert directional["max_asymmetry_ratio"] > neutral["max_asymmetry_ratio"]
    assert directional["max_asymmetry_ratio"] <= 4.25


def test_near_equal_directional_geometry_can_beat_centered_default():
    centered = {
        "skew_pct": 0.0,
        "range_quality_score": 82.0,
        "profit_score": 82.0,
        "regime_alignment_score": 60.0,
        "forecast": {"expected_net_usd": 10.0},
    }
    directional = {
        "skew_pct": -1.5,
        "range_quality_score": 80.0,
        "profit_score": 80.0,
        "regime_alignment_score": 95.0,
        "forecast": {"expected_net_usd": 9.8},
    }
    best, policy = _select_regime_aware_best(
        [centered, directional],
        {"range_skew_pct": -3.0, "confidence": 70},
    )
    assert best["skew_pct"] < 0
    assert policy["selected_placement"] == "LOWER_RATIO_SKEW"
    assert policy["directional_preference_applied"] is True
    assert policy["centered_expected_net_usd"] == 10.0
    assert "Skewed range won" in policy["placement_reason"]


def test_centered_range_remains_valid_when_directional_conviction_is_weak():
    centered = {
        "skew_pct": 0.0,
        "range_quality_score": 85.0,
        "profit_score": 85.0,
        "regime_alignment_score": 95.0,
        "forecast": {"expected_net_usd": 10.0},
    }
    skewed = {
        "skew_pct": 0.75,
        "range_quality_score": 84.0,
        "profit_score": 84.0,
        "regime_alignment_score": 94.0,
        "forecast": {"expected_net_usd": 9.9},
    }
    best, policy = _select_regime_aware_best(
        [centered, skewed],
        {"range_skew_pct": 0.4, "confidence": 70},
    )
    assert best["skew_pct"] == 0.0
    assert policy["selected_placement"] == "CENTERED"
    assert "low directional conviction" in policy["placement_reason"]


def test_nonstable_candidate_advertises_pair_history_requirement_before_profit_lab():
    universe = CandidateUniverse(SimpleNamespace(), FakeStore(), SimpleNamespace())
    readiness = universe._profit_lab_readiness(_row("QNT/WETH"), "ETHEREUM")
    assert readiness["ready"] is False
    assert readiness["status"] == "PAIR_HISTORY_REQUIRED"
    assert readiness["label"] == "HISTORY NEEDED"


def test_stable_quoted_candidate_has_direct_profit_history_path():
    universe = CandidateUniverse(SimpleNamespace(), FakeStore(), SimpleNamespace())
    readiness = universe._profit_lab_readiness(_row("WETH/USDC"), "ETHEREUM")
    assert readiness["ready"] is True
    assert readiness["status"] == "DIRECT_HISTORY_PATH"
    assert readiness["label"] == "DIRECT PATH"


def test_fresh_cached_pair_history_promotes_nonstable_candidate_to_profit_ready():
    store = FakeStore()
    address = "0x" + "b" * 40
    now = int(time.time())
    store.settings[f"profit:history:v09642:ETHEREUM:{address}:hour"] = {
        "provider": "TEST_PAIR_RATIO",
        "candles": [
            {"timestamp": now - (47 - i) * 3600, "close": 10.0}
            for i in range(48)
        ],
    }
    universe = CandidateUniverse(SimpleNamespace(), store, SimpleNamespace())
    readiness = universe._profit_lab_readiness(_row("QNT/WETH", address), "ETHEREUM")
    assert readiness["ready"] is True
    assert readiness["status"] == "READY_CACHED_HISTORY"
    assert readiness["history_samples"] == 48


def test_recent_background_history_failure_is_visible_before_clickthrough():
    store = FakeStore()
    address = "0x" + "c" * 40
    store.settings[f"profit:evidence:status:ETHEREUM:{address}"] = {
        "ok": False,
        "status": "FAILED",
        "checked_at": time.time(),
        "error": "dual-token pair ratio unavailable",
    }
    universe = CandidateUniverse(SimpleNamespace(), store, SimpleNamespace())
    readiness = universe._profit_lab_readiness(_row("ZERO/WETH", address), "ETHEREUM")
    assert readiness["ready"] is False
    assert readiness["status"] == "HISTORY_FAILED"
    assert "dual-token" in readiness["reason"]


def test_v09721_ui_and_version_contracts_are_explicit():
    root = Path(__file__).parents[1]
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    product = (root / "lp_manager" / "product.py").read_text(encoding="utf-8")
    engine = (root / "lp_manager" / "profit_engine.py").read_text(encoding="utf-8")
    warmer = (root / "lp_manager" / "evidence_warmer.py").read_text(encoding="utf-8")

    assert 'APP_VERSION = "0.9.7.5.2"' in product
    assert "/static/styles.css?v=0.9.7.5.2" in html
    assert "/static/app.js?v=0.9.7.5.2" in html
    assert "range quality" in js.lower()
    assert "Range placement:" in js
    assert "profit_lab_readiness" in js
    assert "RELATIVE_RANGE_GEOMETRY_QUALITY_NOT_OPPORTUNITY_SCORE" in engine
    assert "profit:evidence:status:" in warmer
