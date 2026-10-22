from __future__ import annotations

import math

import pytest

from lp_manager.risk_engine import assess_pool_risk
from lp_manager.portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN


def _healthy_candidate():
    return {
        "chain_quality": 95,
        "protocol_quality": 95,
        "asset_conviction": 95,
        "token_quality": 95,
        "liquidity_stability": 95,
        "fee_consistency": 95,
        "pool_age_days": 180,
        "tvl_usd": 10_000_000,
        "historical_volatility": 10,
        "liquidity_concentration_risk": 5,
        "contract_risk": 5,
        "stablecoin_risk": 5,
        "gas_drag_pct": 0.1,
        "exit_liquidity_score": 95,
    }


def test_healthy_core_and_tactical_candidates_remain_eligible():
    for sleeve in (CORE_INCOME, TACTICAL_CAMPAIGN):
        assessment = assess_pool_risk(_healthy_candidate(), sleeve=sleeve)
        assert assessment["eligible"] is True
        assert "INVALID_NUMERIC_RISK_EVIDENCE" not in assessment["blockers"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "nan", "inf", "-inf"])
@pytest.mark.parametrize("field", ["contract_risk", "chain_quality", "tvl_usd", "pool_age_days", "gas_drag_pct"])
def test_nonfinite_risk_evidence_is_never_eligible(field, value):
    candidate = _healthy_candidate()
    candidate[field] = value
    for sleeve in (CORE_INCOME, TACTICAL_CAMPAIGN):
        assessment = assess_pool_risk(candidate, sleeve=sleeve)
        assert assessment["eligible"] is False
        assert "INVALID_NUMERIC_RISK_EVIDENCE" in assessment["blockers"]
        assert f"INVALID_NUMERIC_FIELD:{field}" in assessment["evidence"]
        assert math.isfinite(assessment["overall_risk"])


def test_malformed_numeric_evidence_fails_closed():
    candidate = _healthy_candidate()
    candidate["contract_risk"] = "not-a-number"
    assessment = assess_pool_risk(candidate, sleeve=CORE_INCOME)
    assert assessment["eligible"] is False
    assert "INVALID_NUMERIC_RISK_EVIDENCE" in assessment["blockers"]


def test_missing_optional_numeric_evidence_preserves_existing_default_policy():
    candidate = _healthy_candidate()
    candidate.pop("stablecoin_risk")
    candidate["gas_drag_pct"] = ""
    assessment = assess_pool_risk(candidate, sleeve=CORE_INCOME)
    assert "INVALID_NUMERIC_RISK_EVIDENCE" not in assessment["blockers"]
