import time

import pytest

from lp_manager.candidate_universe import CandidateUniverse
from lp_manager.economics_engine import estimate_lp_economics
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard
from lp_manager.product import ECONOMICS_MODEL_VERSION


def _hlx_pool():
    return {
        "pair": "HLX/USDC",
        "tvl_usd": 349_500.0,
        "volume_24h_usd": 410_400.0,
        "fee_tier": 3000,
        "fee_tier_bps": 30.0,
        "economic_validation": "CROSS_VALIDATED",
        "base_token": {"address": "0x" + "1" * 40, "symbol": "HLX"},
        "quote_token": {"address": "0x" + "2" * 40, "symbol": "USDC"},
        "base_token_price_usd": 0.0445,
        "quote_token_price_usd": 1.0,
    }


def test_current_tick_liquidity_signal_is_bounded_and_loses_authority_with_horizon(monkeypatch):
    monkeypatch.setattr(
        "lp_manager.economics_engine.active_liquidity_share_for_capital",
        lambda *args, **kwargs: {"share": 0.10, "method": "CURRENT_ACTIVE_LIQUIDITY_PROXY"},
    )
    pool = _hlx_pool()
    results = {}
    for days in (1.0, 3.0, 7.0, 30.0):
        results[days] = estimate_lp_economics(
            pool,
            capital=1000.0,
            active_time_pct=100.0,
            width_pct=8.0,
            lower_price=0.043,
            upper_price=0.047,
            horizon_days=days,
        )

    weights = [results[d]["fee_share_blend"]["current_tick_weight"] for d in (1.0, 3.0, 7.0, 30.0)]
    assert weights == sorted(weights, reverse=True)
    assert results[1.0]["fee_share_blend"]["current_tick_raw_share_pct"] == pytest.approx(10.0)
    assert results[1.0]["fee_share_blend"]["current_tick_capped_share_pct"] < 10.0
    assert results[1.0]["gross_apr_pct"] > results[7.0]["gross_apr_pct"] > results[30.0]["gross_apr_pct"]

    # With model-only evidence, even an absurd 10% instantaneous active-liquidity
    # share cannot become a 20x+ whole-pool expected APR.
    pool_apr = pool["volume_24h_usd"] * 0.003 / pool["tvl_usd"] * 365.0 * 100.0
    assert results[1.0]["gross_apr_pct"] < pool_apr * 4.0
    assert results[7.0]["gross_apr_pct"] < pool_apr * 3.0


def test_candidate_fee_apr_proxy_uses_canonical_derived_pool_math_when_fee_tier_is_known():
    metrics = CandidateUniverse._discovery_metrics(_hlx_pool())

    expected = 410_400.0 * 0.003 / 349_500.0 * 365.0 * 100.0
    assert metrics["gross_fee_apr_proxy"] == pytest.approx(round(expected, 1))
    assert metrics["fee_apr_proxy_evidence_class"] == "DERIVED_VOLUME_FEE_TIER_TVL"
    assert metrics["fee_apr_proxy_observed"] is False


def test_candidate_fee_apr_proxy_prefers_actual_fee_observation():
    pool = _hlx_pool()
    pool["fees_24h_usd"] = 1000.0
    metrics = CandidateUniverse._discovery_metrics(pool)

    expected = 1000.0 / 349_500.0 * 365.0 * 100.0
    assert metrics["gross_fee_apr_proxy"] == pytest.approx(round(expected, 1))
    assert metrics["fee_apr_proxy_evidence_class"] == "OBSERVED_POOL_FEES_TVL"
    assert metrics["fee_apr_proxy_observed"] is True


def test_old_deep_snapshot_requires_recheck_under_new_economics_contract():
    now = time.time()
    legacy = OpportunityLeaderboard._deep_state(
        {
            "created_at": now,
            "expected_net_usd": 100.0,
            "capital_usd": 1000.0,
            "horizon_days": 7.0,
            "economics_evidence_class": "MODELLED_CONCENTRATED_POSITION",
        },
        now,
    )
    assert legacy["status"] == "DEEP_RECHECK_REQUIRED"
    assert legacy["allocation_confirmed"] is False
    assert legacy["fresh"] is False

    current = OpportunityLeaderboard._deep_state(
        {
            "created_at": now,
            "expected_net_usd": 25.0,
            "capital_usd": 1000.0,
            "horizon_days": 7.0,
            "economics_model_version": ECONOMICS_MODEL_VERSION,
            "economics_evidence_class": "MODELLED_CONCENTRATED_POSITION",
        },
        now,
    )
    assert current["status"] == "DEEP_PROFITABLE"
    assert current["allocation_confirmed"] is True
    assert current["fresh"] is True


def test_fee_share_blend_contract_is_explicit_in_source():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    engine = (root / "lp_manager" / "economics_engine.py").read_text(encoding="utf-8")

    assert "HORIZON_BLEND_ACTIVE_TVL_RANGE" in engine
    assert "Current-tick weight" in app
    assert "Pool APR · 24h (" in app
    assert "Provider degradation (non-blocking)" in app
    assert "DEEP_RECHECK_REQUIRED" in app
