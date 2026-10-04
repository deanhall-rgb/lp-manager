from pathlib import Path

from lp_manager.asset_lens import pool_price_lens
from lp_manager.candidate_universe import CandidateUniverse


def test_fast_shared_refresh_can_skip_gecko_without_disabling_manual_universe():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    cu = (root / "lp_manager" / "candidate_universe.py").read_text(encoding="utf-8")

    assert "include_gecko: bool = True" in cu
    assert "SKIPPED_CONSUMER_PROFILE" in cu
    assert "graph_limit=200,shortlist_limit=24,validate_limit=6,include_gecko=False" in api
    assert "_UNIVERSE_FRESH_SECONDS = 300.0" in api


def test_browser_assets_are_cache_busted_and_no_store():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "disable_browser_asset_cache" in api
    assert "no-store, no-cache, must-revalidate, max-age=0" in api
    assert "/static/styles.css?v=0.9.7.4.1" in html
    assert "/static/app.js?v=0.9.7.4.1" in html
    assert 'id="app-version-label"' in html


def test_wallet_refresh_skips_duplicate_full_rpc_health_pass():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    assert "live.refresh_wallet(include_health=False)" in api


def test_wpol_uses_execution_price_not_provider_market_cap_projection():
    pool = {
        "base_token":{"symbol":"WPOL"},
        "quote_token":{"symbol":"USDT0"},
        "base_token_price_usd":0.112851,
        "market_cap_usd":20_000_000,
        "fdv_usd":20_000_000,
        "price_unit":"USDT0_PER_WPOL",
        "price_unit_label":"USDT0 per WPOL",
    }
    lens = pool_price_lens(pool, lower=0.107, upper=0.121, current=0.112851)
    assert lens["primary_display"] == "TOKEN_PRICE"


def test_small_campaign_token_keeps_market_cap_as_secondary_context():
    pool = {
        "base_token":{"symbol":"CASHCAT"},
        "quote_token":{"symbol":"WETH"},
        "base_token_price_usd":0.001,
        "market_cap_usd":5_000_000,
    }
    lens = pool_price_lens(pool, lower=0.0009, upper=0.0011, current=0.001)
    assert lens["primary_display"] == "TOKEN_PRICE"
    assert lens["secondary_display"] == "MARKET_CAP"
    assert lens["lower_market_cap_usd"] < lens["upper_market_cap_usd"]
