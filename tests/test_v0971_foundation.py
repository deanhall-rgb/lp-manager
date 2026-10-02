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


def test_product_metadata_has_one_release_identity():
    assert APP_VERSION == "0.9.7.1"
    assert APP_DISPLAY_VERSION == "v0.9.7.1"
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
