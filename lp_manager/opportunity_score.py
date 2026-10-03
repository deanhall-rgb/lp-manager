from __future__ import annotations

import math
from typing import Any

SCORE_VERSION = "OPPORTUNITY_SCORE_V1"

WEIGHTS = {
    "net_economics": 0.30,
    "range_durability": 0.15,
    "liquidity_quality": 0.15,
    "activity_quality": 0.10,
    "risk_quality": 0.15,
    "evidence_quality": 0.15,
}

VALIDATION_STATES = {"CROSS_VALIDATED", "LIVE_VALIDATED", "TVL_MISMATCH"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _log_scale(value: float, floor: float, *, base_score: float = 20.0, per_decade: float = 25.0) -> float:
    value = max(0.0, _f(value))
    floor = max(1e-9, _f(floor, 1.0))
    if value <= 0:
        return 0.0
    return _clamp(base_score + per_decade * math.log10(max(value, floor / 10.0) / floor))


def _return_score(monthly_net_pct: float) -> float:
    """Smoothly reward positive operating return without letting APR dominate."""
    x = max(0.0, _f(monthly_net_pct))
    if x <= 0:
        return 0.0
    # 1%/mo ~= 25, 3% ~= 50, 9% ~= 75; asymptotically approaches 100.
    return _clamp(100.0 * x / (x + 3.0))


def _turnover_quality(turnover: float) -> float:
    """Prefer active but believable turnover; extreme churn is not free quality."""
    t = max(0.0, _f(turnover))
    if t <= 0:
        return 0.0
    if t < 0.03:
        return _clamp(t / 0.03 * 35.0)
    if t < 0.10:
        return 35.0 + (t - 0.03) / 0.07 * 35.0
    if t <= 4.0:
        return 100.0
    if t <= 10.0:
        return 100.0 - (t - 4.0) / 6.0 * 30.0
    if t <= 30.0:
        return 70.0 - (t - 10.0) / 20.0 * 45.0
    return 15.0


def _deep_is_fresh(deep: dict[str, Any]) -> bool:
    return bool(deep and deep.get("fresh") and str(deep.get("status") or "").upper() != "DEEP_STALE")


def _deep_monthly_net_pct(deep: dict[str, Any]) -> float | None:
    if not _deep_is_fresh(deep):
        return None
    capital = max(0.0, _f(deep.get("capital_usd")))
    horizon = max(0.0, _f(deep.get("horizon_days")))
    net = _f(deep.get("expected_net_usd"))
    if capital <= 0 or horizon <= 0:
        return None
    return net / capital * 100.0 * (30.0 / horizon)


def _quick_monthly_net_pct(row: dict[str, Any]) -> float | None:
    econ = dict(row.get("quick_economics") or {})
    for key in ("estimated_net_month_pct", "operating_net_month_pct", "expected_net_month_pct"):
        if econ.get(key) is not None:
            return _f(econ.get(key))
    amount = _f(econ.get("estimated_operating_net_month_usd"), _f(econ.get("estimated_net_month_usd")))
    basis = _f(econ.get("capital_usd"), _f(econ.get("basis_capital_usd"), 1000.0))
    if amount and basis > 0:
        return amount / basis * 100.0
    return None


def score_opportunity(row: dict[str, Any]) -> dict[str, Any]:
    """Transparent 0-100 cross-pool score.

    The score ranks current opportunity quality, not raw APR. Each component is
    independently inspectable. Missing deep Profit Lab evidence reduces evidence
    quality and caps the total score rather than being silently treated as fresh.
    """
    deep = dict(row.get("deep_analysis") or {})
    readiness = dict(row.get("profit_lab_readiness") or {})
    freshness = dict(row.get("freshness") or {})

    tvl = max(0.0, _f(row.get("tvl_usd")))
    volume = max(0.0, _f(row.get("volume_24h_usd")))
    turnover = _f(row.get("turnover_24h"), volume / max(tvl, 1.0))
    fee_apr = max(0.0, _f(row.get("fee_apr_proxy")))
    validation = str(row.get("economic_validation") or "").upper()
    deep_status = str(deep.get("status") or "NOT_ANALYSED").upper()

    notes: list[str] = []

    # 1) Net economics: deep net at its analysed capital/horizon is preferred.
    deep_monthly = _deep_monthly_net_pct(deep)
    quick_monthly = _quick_monthly_net_pct(row)
    if deep_monthly is not None:
        economics_score = _return_score(deep_monthly)
        economics_basis = "DEEP_NET"
        notes.append(f"Deep economics imply {deep_monthly:.2f}% net per 30d equivalent.")
    elif quick_monthly is not None:
        economics_score = _return_score(quick_monthly) * 0.85
        economics_basis = "SCREEN_NET"
        notes.append(f"Screen economics imply {quick_monthly:.2f}% net/month; deep confirmation pending.")
    elif fee_apr > 0:
        gross_monthly = fee_apr / 12.0
        economics_score = min(60.0, _return_score(gross_monthly) * 0.70)
        economics_basis = "GROSS_FEE_PROXY"
        notes.append("Only gross fee proxy is available; costs/range durability are not yet fully confirmed.")
    else:
        economics_score = 10.0
        economics_basis = "INSUFFICIENT"
        notes.append("Net economics are not yet sufficiently evidenced.")

    # 2) Range durability: deep range quality is the authoritative signal when present.
    range_quality = _f(deep.get("range_quality_score"))
    if _deep_is_fresh(deep) and range_quality > 0:
        durability_score = _clamp(range_quality)
        durability_basis = "DEEP_RANGE_QUALITY"
    elif readiness.get("ready"):
        durability_score = 50.0
        durability_basis = "READY_NOT_ANALYSED"
    else:
        durability_score = 30.0
        durability_basis = "HISTORY_PENDING"

    # 3) Liquidity quality.
    liquidity_score = _log_scale(tvl, 25_000.0)
    if tvl < 25_000:
        notes.append("Liquidity is below the normal research-ready floor.")

    # 4) Sustainable activity quality: volume + believable turnover.
    volume_score = _log_scale(volume, 10_000.0)
    turnover_score = _turnover_quality(turnover)
    activity_score = _clamp(0.55 * volume_score + 0.45 * turnover_score)
    if turnover > 10:
        notes.append(f"Turnover is extreme at {turnover:.1f}x TVL/24h, so activity quality is discounted.")

    # 5) Risk/friction quality starts high and is explicitly penalised.
    risk_score = 100.0
    if validation == "TVL_MISMATCH":
        risk_score -= 18.0
        notes.append("Provider TVL mismatch increases uncertainty.")
    elif validation == "LIVE_VALIDATED":
        risk_score -= 4.0
    elif validation not in VALIDATION_STATES:
        risk_score -= 35.0

    if tvl < 50_000:
        risk_score -= 25.0
    elif tvl < 100_000:
        risk_score -= 15.0
    elif tvl < 250_000:
        risk_score -= 7.0

    if turnover > 30:
        risk_score -= 30.0
    elif turnover > 10:
        risk_score -= 20.0
    elif turnover > 5:
        risk_score -= 10.0
    elif 0 < turnover < 0.03:
        risk_score -= 10.0

    fees = max(0.0, _f(deep.get("expected_fees_usd")))
    fixed_cost = max(0.0, _f(deep.get("expected_intervention_cost_usd")))
    if fees > 0 and fixed_cost > 0:
        cost_ratio = fixed_cost / fees
        if cost_ratio >= 0.50:
            risk_score -= 25.0
        elif cost_ratio >= 0.25:
            risk_score -= 15.0
        elif cost_ratio >= 0.10:
            risk_score -= 8.0
        if cost_ratio >= 0.25:
            notes.append(f"Expected intervention cost consumes {cost_ratio*100:.0f}% of forecast fees.")

    if bool(row.get("portfolio_overlap")):
        risk_score -= 8.0
        notes.append("Existing portfolio exposure reduces incremental diversification quality.")
    risk_score = _clamp(risk_score)

    # 6) Evidence quality combines freshness, provider validation, Profit Lab readiness and deep maturity.
    freshness_state = str(freshness.get("state") or "UNKNOWN").upper()
    fresh_score = {
        "FRESH": 100.0,
        "RECENT": 90.0,
        "AGING": 65.0,
        "STALE": 20.0,
        "UNKNOWN": 35.0,
    }.get(freshness_state, 35.0)
    validation_score = {
        "CROSS_VALIDATED": 100.0,
        "LIVE_VALIDATED": 85.0,
        "TVL_MISMATCH": 65.0,
        "UNVERIFIED": 30.0,
    }.get(validation, 25.0)
    readiness_score = 100.0 if readiness.get("ready") else 35.0 if str(readiness.get("status") or "").upper() == "HISTORY_FAILED" else 55.0
    deep_score = {
        "DEEP_PROFITABLE": 100.0,
        "DEEP_NON_POSITIVE": 100.0,
        "DEEP_CAPITAL_NON_POSITIVE": 100.0,
        "DEEP_CAPITAL_UNVERIFIED": 80.0,
        "DEEP_STALE": 45.0,
        "NOT_ANALYSED": 45.0,
    }.get(deep_status, 45.0)
    evidence_score = _clamp(0.25 * fresh_score + 0.25 * validation_score + 0.20 * readiness_score + 0.30 * deep_score)

    components = {
        "net_economics": round(economics_score, 1),
        "range_durability": round(durability_score, 1),
        "liquidity_quality": round(liquidity_score, 1),
        "activity_quality": round(activity_score, 1),
        "risk_quality": round(risk_score, 1),
        "evidence_quality": round(evidence_score, 1),
    }
    raw = sum(components[name] * weight for name, weight in WEIGHTS.items())

    # Evidence maturity caps stop an unanalysed or stale pool from masquerading as
    # equally decision-ready to a fresh deep-analysed candidate.
    score_cap = 100.0
    cap_reason = ""
    if validation not in VALIDATION_STATES:
        score_cap, cap_reason = 50.0, "market validation incomplete"
    elif deep_status == "DEEP_NON_POSITIVE":
        score_cap, cap_reason = 55.0, "fresh deep economics are non-positive"
    elif deep_status == "DEEP_STALE":
        score_cap, cap_reason = 72.0, "deep analysis is stale"
    elif deep_status in {"NOT_ANALYSED", ""}:
        score_cap = 79.0 if readiness.get("ready") else 68.0
        cap_reason = "fresh deep Profit Lab analysis is still required"
    elif not readiness.get("ready"):
        score_cap, cap_reason = 72.0, "Profit Lab evidence is incomplete"

    final_score = round(min(raw, score_cap), 1)
    confidence = "HIGH" if evidence_score >= 80 and _deep_is_fresh(deep) else "MEDIUM" if evidence_score >= 60 else "LOW"

    weighted = {name: round(components[name] * WEIGHTS[name], 1) for name in WEIGHTS}
    return {
        "version": SCORE_VERSION,
        "score": final_score,
        "raw_score": round(raw, 1),
        "score_cap": round(score_cap, 1),
        "cap_reason": cap_reason,
        "confidence": confidence,
        "basis": economics_basis,
        "monthly_net_pct_equivalent": round(deep_monthly if deep_monthly is not None else quick_monthly, 3) if (deep_monthly is not None or quick_monthly is not None) else None,
        "components": components,
        "weighted_components": weighted,
        "weights": {name: int(weight * 100) for name, weight in WEIGHTS.items()},
        "durability_basis": durability_basis,
        "notes": notes[:6],
    }
