from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

CORE_INCOME = "CORE_INCOME"
TACTICAL_CAMPAIGN = "TACTICAL_CAMPAIGN"


@dataclass(frozen=True)
class SleevePolicy:
    name: str
    objective: str
    capital_role: str
    preferred_hold_days: float
    min_hold_days: float
    max_hold_days: float
    strategy_review_seconds: int
    ai_review_seconds: int
    watch_edge_pct: float
    intervene_edge_pct: float
    target_monthly_net_min_pct: float | None
    target_monthly_net_max_pct: float | None
    target_campaign_net_pct: float | None
    minimum_asset_conviction: float
    minimum_liquidity_stability: float
    minimum_fee_consistency: float
    minimum_chain_quality: float
    minimum_history_days: float
    desired_in_range_probability: float
    range_style: str
    reinvestment_style: str
    exit_style: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


POLICIES: dict[str, SleevePolicy] = {
    CORE_INCOME: SleevePolicy(
        name=CORE_INCOME,
        objective="Durable fee income with low intervention and acceptable long-term inventory exposure.",
        capital_role="PRIMARY",
        preferred_hold_days=30.0,
        min_hold_days=14.0,
        max_hold_days=90.0,
        strategy_review_seconds=12 * 3600,
        ai_review_seconds=24 * 3600,
        watch_edge_pct=5.0,
        intervene_edge_pct=2.0,
        target_monthly_net_min_pct=8.0,
        target_monthly_net_max_pct=20.0,
        target_campaign_net_pct=None,
        minimum_asset_conviction=80.0,
        minimum_liquidity_stability=75.0,
        minimum_fee_consistency=65.0,
        minimum_chain_quality=80.0,
        minimum_history_days=30.0,
        desired_in_range_probability=70.0,
        range_style="WIDE_DURABLE_ASYMMETRIC_WHEN_DIRECTIONAL",
        reinvestment_style="COMPOUND_WHEN_FEES_MATERIALLY_EXCEED_EXECUTION_COST",
        exit_style="THESIS_AND_CAPITAL_EFFICIENCY_FIRST",
    ),
    TACTICAL_CAMPAIGN: SleevePolicy(
        name=TACTICAL_CAMPAIGN,
        objective="Higher fee/profit campaigns with smaller capital, tighter monitoring and explicit exit/redeployment plans.",
        capital_role="SATELLITE",
        preferred_hold_days=3.0,
        min_hold_days=0.15,
        max_hold_days=10.0,
        strategy_review_seconds=4 * 3600,
        ai_review_seconds=12 * 3600,
        watch_edge_pct=10.0,
        intervene_edge_pct=4.0,
        target_monthly_net_min_pct=None,
        target_monthly_net_max_pct=None,
        target_campaign_net_pct=5.0,
        minimum_asset_conviction=55.0,
        minimum_liquidity_stability=45.0,
        minimum_fee_consistency=45.0,
        minimum_chain_quality=65.0,
        minimum_history_days=2.0,
        desired_in_range_probability=50.0,
        range_style="ACTIVE_CAMPAIGN_RANGE",
        reinvestment_style="USUALLY_BANK_FEES_UNLESS_CAMPAIGN_PLAN_EXPLICITLY_COMPOUNDS",
        exit_style="PROFIT_TARGET_OR_EDGE_INVALIDATION",
    ),
}


def policy_for(value: str | None) -> SleevePolicy:
    key = str(value or TACTICAL_CAMPAIGN).upper()
    return POLICIES.get(key, POLICIES[TACTICAL_CAMPAIGN])


def policies_payload() -> dict[str, dict[str, Any]]:
    return {name: policy.to_dict() for name, policy in POLICIES.items()}
