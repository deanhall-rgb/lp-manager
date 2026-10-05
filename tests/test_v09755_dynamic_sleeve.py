from pathlib import Path

from lp_manager.live_scout import preliminary_pool_evaluation
from lp_manager.risk_engine import assess_pool_risk


def _pool(pair, base, quote, tvl, volume):
    return {
        "pair": pair,
        "base_token": {"symbol": base},
        "quote_token": {"symbol": quote},
        "protocol": "UNISWAP_V3",
        "tvl_usd": tvl,
        "volume_24h_usd": volume,
    }


def test_non_mature_campaign_pair_does_not_fall_back_to_core_by_fractional_prescore():
    row = preliminary_pool_evaluation(
        _pool("WLD/USDC", "WLD", "USDC", 60_000, 30_000)
    )
    assert row["pair_policy_sleeve"] == "TACTICAL_CAMPAIGN"
    assert row["preferred_sleeve"] == "TACTICAL_CAMPAIGN"
    assert row["risk_tactical"]["eligible"] is True


def test_mature_pair_can_be_tactical_when_core_is_too_thin_but_tactical_is_viable():
    row = preliminary_pool_evaluation(
        _pool("OP/USDC", "OP", "USDC", 98_000, 70_000)
    )
    assert row["pair_policy_sleeve"] == "CORE_INCOME"
    assert row["risk_core"]["eligible"] is False
    assert "CORE_TVL_TOO_LOW" in row["risk_core"]["blockers"]
    assert row["risk_tactical"]["eligible"] is True
    assert row["preferred_sleeve"] == "TACTICAL_CAMPAIGN"
    assert row["sleeve_reason"] == "MATURE_PAIR_TACTICAL_OVERRIDE"


def test_tactical_liquidity_floor_is_50k_with_thin_evidence_below_150k():
    candidate = {
        "chain_quality": 90,
        "protocol_quality": 92,
        "asset_conviction": 82,
        "token_quality": 82,
        "liquidity_stability": 50,
        "exit_liquidity_score": 50,
        "fee_consistency": 55,
        "historical_volatility": 0,
        "liquidity_concentration_risk": 50,
        "contract_risk": 12,
        "stablecoin_risk": 12,
        "gas_drag_pct": 0,
        "audited_contract": True,
        "pool_age_known": False,
        "tvl_usd": 98_000,
    }
    viable = assess_pool_risk(candidate, sleeve="TACTICAL_CAMPAIGN")
    assert viable["eligible"] is True
    assert "TACTICAL_TVL_TOO_LOW" not in viable["blockers"]
    assert "TACTICAL_TVL_THIN" in viable["evidence"]

    candidate["tvl_usd"] = 49_999
    too_small = assess_pool_risk(candidate, sleeve="TACTICAL_CAMPAIGN")
    assert too_small["eligible"] is False
    assert "TACTICAL_TVL_TOO_LOW" in too_small["blockers"]


def test_v09755_api_has_no_prescore_winner_fallback_for_sleeve():
    root = Path(__file__).resolve().parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    maintenance = (root / "lp_manager" / "candidate_universe_maintenance.py").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert 'evaluation.get("pair_policy_sleeve") or "TACTICAL_CAMPAIGN"' in api
    assert '"CORE_INCOME" if float(evaluation.get("core_pre_score")' not in api
    assert "include_gecko=False" in maintenance
    assert "/static/app.js?v=0.9.7.6" in html
