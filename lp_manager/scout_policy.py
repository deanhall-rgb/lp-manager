from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN, policy_for


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@dataclass
class ScoutCandidate:
    chain: str
    protocol: str
    pair: str
    pool_address: str | None = None
    tvl_usd: float = 0.0
    volume_24h_usd: float = 0.0
    pool_age_days: float = 0.0
    chain_quality: float = 50.0
    asset_conviction: float = 50.0
    token_quality: float = 50.0
    liquidity_stability: float = 50.0
    fee_consistency: float = 50.0
    in_range_probability_30d: float = 0.0
    expected_monthly_net_pct: float = 0.0
    expected_daily_net_pct: float = 0.0
    historical_volatility: float = 0.0
    gas_drag_pct: float = 0.0
    sentiment_score: float = 0.0        # -1 bearish, 0 neutral, +1 bullish
    momentum_alignment: float = 50.0    # 0..100
    downside_inventory_desirable: bool = False
    upside_inventory_desirable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _target_band_score(value: float, low: float, high: float) -> float:
    if value <= 0:
        return 0.0
    if low <= value <= high:
        return 100.0
    if value < low:
        return _clamp((value / low) * 100.0 if low else 100.0)
    # Excess yield is not automatically better: above the target band can be a risk smell.
    excess = (value - high) / max(high, 1.0)
    return _clamp(100.0 - excess * 35.0, 45.0, 100.0)


def evaluate_core(candidate: ScoutCandidate | dict[str, Any]) -> dict[str, Any]:
    c = candidate.to_dict() if isinstance(candidate, ScoutCandidate) else dict(candidate)
    p = policy_for(CORE_INCOME)
    reasons: list[str] = []
    blockers: list[str] = []

    age = _f(c.get("pool_age_days"))
    conviction = _f(c.get("asset_conviction"))
    stability = _f(c.get("liquidity_stability"))
    consistency = _f(c.get("fee_consistency"))
    chain = _f(c.get("chain_quality"))
    residence = _f(c.get("in_range_probability_30d"))
    monthly = _f(c.get("expected_monthly_net_pct"))
    gas_drag = max(0.0, _f(c.get("gas_drag_pct")))
    tvl = max(0.0, _f(c.get("tvl_usd")))

    if age < p.minimum_history_days:
        blockers.append(f"POOL_HISTORY<{p.minimum_history_days:.0f}D")
    if conviction < p.minimum_asset_conviction:
        blockers.append("ASSET_CONVICTION_TOO_LOW")
    if stability < p.minimum_liquidity_stability:
        blockers.append("LIQUIDITY_NOT_STABLE_ENOUGH")
    if consistency < p.minimum_fee_consistency:
        blockers.append("FEES_NOT_CONSISTENT_ENOUGH")
    if chain < p.minimum_chain_quality:
        blockers.append("CHAIN_QUALITY_TOO_LOW")
    if residence < 55.0:
        blockers.append("LOW_LONG_HORIZON_RANGE_RESIDENCE")
    if tvl < 1_000_000:
        blockers.append("CORE_TVL_TOO_LOW")
    if gas_drag > 2.0:
        blockers.append("EXECUTION_DRAG_TOO_HIGH")

    yield_score = _target_band_score(monthly, p.target_monthly_net_min_pct or 0.0, p.target_monthly_net_max_pct or 100.0)
    score = (
        conviction * 0.20
        + stability * 0.20
        + consistency * 0.15
        + chain * 0.10
        + residence * 0.20
        + yield_score * 0.15
    )
    score -= min(20.0, gas_drag * 5.0)

    if c.get("downside_inventory_desirable"):
        score += 5.0
        reasons.append("DOWNSIDE_INVENTORY_IS_ACCEPTABLE")
    if monthly >= (p.target_monthly_net_min_pct or 0):
        reasons.append("MONTHLY_NET_TARGET_MET")
    if residence >= p.desired_in_range_probability:
        reasons.append("DURABILITY_TARGET_MET")

    return {
        "sleeve": CORE_INCOME,
        "eligible": not blockers,
        "score": round(_clamp(score), 2),
        "blockers": blockers,
        "reasons": reasons,
        "policy": p.to_dict(),
        "management": {
            "range_style": p.range_style,
            "review_seconds": p.strategy_review_seconds,
            "ai_review_seconds": p.ai_review_seconds,
            "reinvestment_style": p.reinvestment_style,
            "inventory_intent": "ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE" if c.get("downside_inventory_desirable") else "BALANCED",
        },
    }


def evaluate_tactical(candidate: ScoutCandidate | dict[str, Any]) -> dict[str, Any]:
    c = candidate.to_dict() if isinstance(candidate, ScoutCandidate) else dict(candidate)
    p = policy_for(TACTICAL_CAMPAIGN)
    blockers: list[str] = []
    reasons: list[str] = []

    age = _f(c.get("pool_age_days"))
    conviction = _f(c.get("asset_conviction"))
    token_quality = _f(c.get("token_quality"))
    stability = _f(c.get("liquidity_stability"))
    consistency = _f(c.get("fee_consistency"))
    chain = _f(c.get("chain_quality"))
    sentiment = _f(c.get("sentiment_score"))
    momentum = _f(c.get("momentum_alignment"))
    daily = _f(c.get("expected_daily_net_pct"))
    gas_drag = max(0.0, _f(c.get("gas_drag_pct")))
    tvl = max(0.0, _f(c.get("tvl_usd")))

    if age < p.minimum_history_days:
        blockers.append("INSUFFICIENT_CAMPAIGN_HISTORY")
    if conviction < p.minimum_asset_conviction:
        blockers.append("ASSET_CONVICTION_TOO_LOW")
    if token_quality < 50.0:
        blockers.append("TOKEN_QUALITY_TOO_LOW")
    if stability < p.minimum_liquidity_stability:
        blockers.append("LIQUIDITY_TOO_FRAGILE")
    if consistency < p.minimum_fee_consistency:
        blockers.append("FEE_EDGE_TOO_UNSTABLE")
    if chain < p.minimum_chain_quality:
        blockers.append("CHAIN_QUALITY_TOO_LOW")
    if tvl < 150_000:
        blockers.append("TACTICAL_TVL_TOO_LOW")
    if gas_drag > 4.0:
        blockers.append("EXECUTION_DRAG_TOO_HIGH")
    # Tactical positions require directional evidence when holding the volatile asset is part of the downside case.
    if c.get("downside_inventory_desirable") and (sentiment < 0.10 or momentum < 55.0):
        blockers.append("BULLISH_THESIS_NOT_CONFIRMED")

    fee_opportunity = _clamp(daily * 35.0)  # prototype normalisation, calibrated later from observed campaigns
    sentiment_score = _clamp(50.0 + sentiment * 50.0)
    score = (
        fee_opportunity * 0.25
        + sentiment_score * 0.15
        + momentum * 0.20
        + stability * 0.15
        + consistency * 0.10
        + conviction * 0.10
        + chain * 0.05
    )
    score -= min(20.0, gas_drag * 4.0)

    if sentiment >= 0.25:
        reasons.append("POSITIVE_SENTIMENT")
    if momentum >= 65.0:
        reasons.append("MOMENTUM_ALIGNED")
    if daily >= 0.5:
        reasons.append("STRONG_SHORT_HORIZON_NET_YIELD")

    return {
        "sleeve": TACTICAL_CAMPAIGN,
        "eligible": not blockers,
        "score": round(_clamp(score), 2),
        "blockers": blockers,
        "reasons": reasons,
        "policy": p.to_dict(),
        "management": {
            "range_style": p.range_style,
            "review_seconds": p.strategy_review_seconds,
            "ai_review_seconds": p.ai_review_seconds,
            "reinvestment_style": p.reinvestment_style,
            "campaign_goal": "MAXIMISE_NET_PROFIT_AND_REDEPLOY_CAPITAL",
        },
    }


def evaluate_candidate(candidate: ScoutCandidate | dict[str, Any]) -> dict[str, Any]:
    core = evaluate_core(candidate)
    tactical = evaluate_tactical(candidate)
    eligible = [x for x in (core, tactical) if x["eligible"]]
    preferred = max(eligible, key=lambda x: x["score"], default=max((core, tactical), key=lambda x: x["score"]))
    return {
        "preferred_sleeve": preferred["sleeve"] if preferred["eligible"] else None,
        "preferred_score": preferred["score"],
        "core": core,
        "tactical": tactical,
    }
