from pathlib import Path

from lp_manager.asset_registry import (
    STABLE_SYMBOLS,
    CORE_MAJOR_SYMBOLS,
    is_stable_symbol,
    should_use_market_cap_display,
)
from lp_manager.discovery_lab import _normalise_dex_pair, _normalise_graph_pool
from lp_manager.opportunity_model import (
    PROTOCOL_UNISWAP_V3,
    PROTOCOL_UNISWAP_V4,
    canonical_opportunity,
    freshness_state,
)
from lp_manager.product import APP_VERSION, APP_DISPLAY_VERSION, OPPORTUNITY_SCHEMA_VERSION
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard
import time


def test_product_metadata_has_one_release_identity():
    assert APP_VERSION == "0.9.7.3.1"
    assert APP_DISPLAY_VERSION == "v0.9.7.3.1"
    assert OPPORTUNITY_SCHEMA_VERSION == "1.0"


def test_asset_registry_covers_current_stables_and_preserves_core_semantics():
    assert "USDT0" in STABLE_SYMBOLS
    assert is_stable_symbol("usdt0")
    assert "WETH" in CORE_MAJOR_SYMBOLS
    assert "USDC" in CORE_MAJOR_SYMBOLS
    # Network tokens are display majors but are not silently promoted to the
    # Core inventory class in this foundation patch.
    assert "WPOL" not in CORE_MAJOR_SYMBOLS


def test_market_cap_policy_is_canonical():
    assert not should_use_market_cap_display("WPOL")
    assert not should_use_market_cap_display("USDT0")
    assert should_use_market_cap_display("CASHCAT")


def test_canonical_opportunity_preserves_v3_shape_and_adds_identity():
    row = canonical_opportunity({
        "chain": "ethereum",
        "pool_address": "0x" + "a" * 40,
        "protocol": "UNISWAP_V3",
        "pair": "WETH/USDC",
        "base_token": {"symbol": "weth"},
        "quote_token": {"symbol": "usdc"},
        "tvl_usd": "1000000",
        "volume_24h_usd": "500000",
        "source_updated_at": 1000,
    }, now=1100)
    assert row["chain"] == "ETHEREUM"
    assert row["protocol"] == PROTOCOL_UNISWAP_V3
    assert row["protocol_version"] == "V3"
    assert row["version"] == "V3"
    assert row["opportunity_id"] == "ETHEREUM:UNISWAP_V3:" + ("0x" + "a" * 40)
    assert row["opportunity_schema_version"] == "1.0"
    assert row["base_token"]["symbol"] == "WETH"
    assert row["quote_token"]["symbol"] == "USDC"
    assert row["freshness"]["market"] == "FRESH"


def test_canonical_opportunity_is_v4_ready_without_claiming_v4_execution():
    row = canonical_opportunity({
        "chain": "base",
        "pool_address": "0x" + "b" * 40,
        "protocol_guess": "UNISWAP_V4",
        "version": "V4",
        "pair": "TOKEN/WETH",
    })
    assert row["protocol"] == PROTOCOL_UNISWAP_V4
    assert row["protocol_version"] == "V4"
    assert row["opportunity_id"].startswith("BASE:UNISWAP_V4:")


def test_freshness_states_are_explicit():
    assert freshness_state(None, now=100)[0] == "UNKNOWN"
    assert freshness_state(90, now=100, fresh_seconds=30, stale_seconds=60)[0] == "FRESH"
    assert freshness_state(50, now=100, fresh_seconds=30, stale_seconds=60)[0] == "AGING"
    assert freshness_state(1, now=100, fresh_seconds=30, stale_seconds=60)[0] == "STALE"


def test_dex_normaliser_emits_canonical_v4_record():
    row = _normalise_dex_pair({
        "chainId": "base",
        "pairAddress": "0x" + "c" * 40,
        "dexId": "uniswap-v4",
        "labels": ["v4"],
        "baseToken": {"address": "0x" + "1" * 40, "symbol": "AAA"},
        "quoteToken": {"address": "0x" + "2" * 40, "symbol": "WETH"},
        "liquidity": {"usd": 123456},
        "volume": {"h24": 98765},
    }, "BASE")
    assert row["protocol"] == PROTOCOL_UNISWAP_V4
    assert row["protocol_version"] == "V4"
    assert row["analysis_status"] == "DISCOVERED"


def test_graph_normaliser_emits_canonical_v3_record():
    row = _normalise_graph_pool({
        "id": "0x" + "d" * 40,
        "feeTier": "3000",
        "totalValueLockedUSD": "1000000",
        "volumeUSD": "999999",
        "token0": {"id": "0x" + "1" * 40, "symbol": "WETH"},
        "token1": {"id": "0x" + "2" * 40, "symbol": "USDC"},
        "poolDayData": [{"date": 1, "volumeUSD": "12345", "feesUSD": "12"}],
    }, "ETHEREUM")
    assert row["protocol"] == PROTOCOL_UNISWAP_V3
    assert row["protocol_version"] == "V3"
    assert row["opportunity_schema_version"] == "1.0"


def test_ui_hides_old_internal_patch_names_and_uses_runtime_version_label():
    root = Path(__file__).parents[1]
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="app-version-label"' in html
    assert "Discovery Lab · v0.9.6.1" not in html
    assert "Candidate Universe · v0.9.6.4.2" not in html
    assert "v0.9.3 thesis overlay" not in js
    assert "direct v0.9.4 accounting" not in js.lower()
    assert "app.display_version" in js


def test_api_exposes_canonical_app_metadata_and_versionless_shared_source():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    assert "version=APP_VERSION" in api
    assert '"app": app_metadata()' in api
    assert '"SHARED_CANDIDATE_UNIVERSE"' in api
    assert "SHARED_CANDIDATE_UNIVERSE_V0964" not in api

class _LeaderboardStore:
    def __init__(self, forecasts=None):
        self.settings = {}
        self.forecasts = list(forecasts or [])

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def list_forecast_snapshots(self, limit=500):
        return list(self.forecasts)[:limit]

    def list_opportunities(self, limit=100):
        return []


class _LeaderboardUniverse:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def cached(self, chain):
        return self.snapshots.get(chain, {"ok": False, "summary": {}, "shortlist": []})


def _leaderboard_candidate(chain, address, pair, score, version="V3"):
    return {
        "chain": chain,
        "pool_address": address,
        "pair": pair,
        "protocol": "UNISWAP_" + version,
        "version": version,
        "tvl_usd": 1000000,
        "volume_24h_usd": 2000000,
        "gross_fee_apr_proxy": 40,
        "discovery_score": score,
        "economic_validation": "CROSS_VALIDATED",
        "research_ready": True,
        "profit_lab_readiness": {"ready": True, "status": "READY_CACHED_HISTORY", "label": "PROFIT READY"},
    }


def _leaderboard_snapshot(chain, rows, age=10):
    return {
        "ok": True,
        "chain": chain,
        "generated_at": time.time() - age,
        "summary": {
            "v3_discovered": len(rows),
            "shortlisted": len(rows),
            "live_validated": len(rows),
            "research_ready": len(rows),
            "profit_lab_ready": len(rows),
        },
        "shortlist": rows,
    }


def test_v0973_persistent_board_combines_cached_chains():
    eth = _leaderboard_candidate("ETHEREUM", "0x" + "1" * 40, "QNT/WETH", 70)
    arb = _leaderboard_candidate("ARBITRUM", "0x" + "2" * 40, "ARB/WETH", 65)
    store = _LeaderboardStore()
    board = OpportunityLeaderboard(store, _LeaderboardUniverse({
        "ETHEREUM": _leaderboard_snapshot("ETHEREUM", [eth]),
        "ARBITRUM": _leaderboard_snapshot("ARBITRUM", [arb]),
    }))
    result = board.rebuild()
    assert result["funnel"]["chains_with_snapshots"] == 2
    assert {x["chain"] for x in result["rows"]} == {"ETHEREUM", "ARBITRUM"}
    assert OpportunityLeaderboard.STORE_KEY in store.settings


def test_v0973_stale_chain_does_not_compete_as_current():
    fresh = _leaderboard_candidate("ETHEREUM", "0x" + "3" * 40, "WETH/USDC", 50)
    stale = _leaderboard_candidate("BASE", "0x" + "4" * 40, "WETH/USDC", 99)
    board = OpportunityLeaderboard(_LeaderboardStore(), _LeaderboardUniverse({
        "ETHEREUM": _leaderboard_snapshot("ETHEREUM", [fresh], 30),
        "BASE": _leaderboard_snapshot("BASE", [stale], 7200),
    }))
    result = board.rebuild()
    assert any(x["chain"] == "ETHEREUM" for x in result["rows"])
    assert not any(x["chain"] == "BASE" for x in result["rows"])


def test_v0973_screen_score_is_provisional_and_v4_ready():
    row = _leaderboard_candidate("BASE", "0x" + "8" * 40, "TOKEN/WETH", 60, "V4")
    result = OpportunityLeaderboard(
        _LeaderboardStore(),
        _LeaderboardUniverse({"BASE": _leaderboard_snapshot("BASE", [row])}),
    ).rebuild()
    assert result["score_is_final_opportunity_score"] is False
    assert result["rows"][0]["score_type"] == "PROVISIONAL_SCREEN_SCORE"
    assert result["rows"][0]["protocol_version"] == "V4"


def test_v0973_ui_has_persistent_leaderboard_surface():
    root = Path(__file__).parents[1]
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="leaderboard-result"' in html
    assert "Live Opportunity Leaderboard" in html
    assert "Screen score" in html
    assert "/static/app.js?v=0.9.7.3.1" in html
    assert "renderOpportunityLeaderboard" in js

def test_v0973_advisor_deep_state_matches_strategy_horizon_not_latest_other_hold():
    address = "0x" + "9" * 40
    now = time.time()
    store = _LeaderboardStore([
        {"chain": "ETHEREUM", "pool_address": address, "created_at": now, "horizon_days": 1, "expected_net_usd": 9},
        {"chain": "ETHEREUM", "pool_address": address, "created_at": now - 1, "horizon_days": 3, "expected_net_usd": -2},
    ])
    board = OpportunityLeaderboard(store, _LeaderboardUniverse({}))
    rows = board.attach_analysis_state([{
        "chain": "ETHEREUM",
        "pool_address": address,
        "pair": "QNT/WETH",
        "sleeve": "TACTICAL_CAMPAIGN",
    }])
    deep = rows[0]["deep_analysis"]
    assert deep["required_horizon_days"] == 3
    assert deep["horizon_match"] is True
    assert deep["status"] == "DEEP_NON_POSITIVE"
    assert deep["expected_net_usd"] == -2

