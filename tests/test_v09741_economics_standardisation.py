from pathlib import Path

import pytest

from lp_manager.asset_lens import pool_price_lens
from lp_manager.fee_metrics import forecast_fee_metrics, pool_apr_24h_benchmark
from lp_manager.opportunity_score import score_opportunity


def test_hlx_pool_apr_24h_matches_external_uniswap_style_benchmark():
    pool = {
        "pair": "HLX/USDC",
        "tvl_usd": 356_500.0,
        "fees_24h_usd": 957.77,
        "economic_validation": "CROSS_VALIDATED",
        "providers": ["GECKOTERMINAL"],
    }
    result = pool_apr_24h_benchmark(pool, fee_tier_bps=30.0)

    assert result["observed"] is True
    assert result["evidence_class"] == "OBSERVED_POOL_FEES_TVL"
    assert result["apr_pct"] == pytest.approx(98.06, abs=0.01)
    assert result["fees_24h_usd"] == pytest.approx(957.77)
    assert result["tvl_usd"] == pytest.approx(356_500.0)


def test_pool_apr_fallback_is_typed_as_derived_not_observed():
    pool = {
        "tvl_usd": 1_000_000.0,
        "volume_24h_usd": 2_000_000.0,
        "fee_tier_bps": 30.0,
        "economic_validation": "LIVE_VALIDATED",
    }
    result = pool_apr_24h_benchmark(pool, fee_tier_bps=30.0)

    assert result["observed"] is False
    assert result["evidence_class"] == "DERIVED_VOLUME_FEE_TIER_TVL"
    assert result["apr_pct"] == pytest.approx(219.0, abs=0.01)


def test_small_token_market_cap_is_secondary_to_executable_price_range():
    pool = {
        "base_token": {"symbol": "HLX"},
        "quote_token": {"symbol": "USDC"},
        "base_token_price_usd": 0.0465,
        "market_cap_usd": 4_650_000_000.0,
        "price_unit": "USDC_PER_HLX",
        "price_unit_label": "USDC per HLX",
    }
    lens = pool_price_lens(pool, lower=0.04430, upper=0.04896, current=0.0465)

    assert lens["primary_display"] == "TOKEN_PRICE"
    assert lens["secondary_display"] == "MARKET_CAP"
    assert lens["unit"] == "USDC_PER_HLX"
    assert lens["lower_market_cap_usd"] < lens["upper_market_cap_usd"]


@pytest.mark.parametrize("horizon_days", [1.0, 3.0, 7.0])
def test_position_forecast_apr_reconciles_to_capital_horizon_and_fixed_cost(horizon_days):
    capital = 500.0
    fee_day = 2.0
    fixed_cost = 1.50
    result = forecast_fee_metrics(
        capital_usd=capital,
        horizon_days=horizon_days,
        expected_fees_usd=fee_day * horizon_days,
        expected_cash_costs_usd=fixed_cost,
    )

    expected_apr = fee_day / capital * 365.0 * 100.0
    assert result["forecast_fee_apr_pct"] == pytest.approx(expected_apr, abs=0.01)
    assert result["expected_net_usd"] == pytest.approx(fee_day * horizon_days - fixed_cost, abs=0.0001)


def _score_row(evidence_class: str, confidence: str):
    return {
        "tvl_usd": 1_000_000.0,
        "volume_24h_usd": 1_500_000.0,
        "turnover_24h": 1.5,
        "fee_apr_proxy": 40.0,
        "economic_validation": "CROSS_VALIDATED",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "freshness": {"state": "FRESH"},
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 500.0,
            "horizon_days": 3.0,
            "expected_fees_usd": 15.0,
            "expected_intervention_cost_usd": 1.5,
            "expected_net_usd": 13.5,
            "range_quality_score": 80.0,
            "economics_evidence_class": evidence_class,
            "economics_confidence": confidence,
        },
    }


def test_opportunity_score_reduces_authority_for_transferred_fee_economics():
    direct = score_opportunity(_score_row("MODELLED_CONCENTRATED_POSITION", "MODERATE"))
    transferred = score_opportunity(_score_row("MODELLED_POSITION_FROM_SCREEN_PRIOR", "LOW"))

    assert direct["economics_authority"] > transferred["economics_authority"]
    assert direct["score"] > transferred["score"]
    assert transferred["score_cap"] <= 82.0


def test_profit_ui_names_pool_and_position_apr_separately_and_keeps_price_primary():
    app = (Path(__file__).resolve().parents[1] / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert "Pool APR · 24h annualised" in app
    assert "Modelled position APR" in app
    assert "Pool 24h fee APR" not in app
    assert "primary_display==='MARKET_CAP'" not in app
    assert "Market cap context" in app
