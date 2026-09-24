from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .v3_math import active_liquidity_share_for_capital


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def infer_fee_tier_bps(pool: dict[str, Any]) -> tuple[float, str]:
    for key in ("fee_tier_bps", "fee_bps", "fee_tier"):
        value = _f(pool.get(key))
        if value > 0:
            # Uniswap V3 fee() returns hundredths of a basis point: 500=5 bps.
            if value >= 100:
                return value / 100.0, "POOL_METADATA"
            return value, "POOL_METADATA"
    text = " ".join(str(pool.get(k) or "") for k in ("name", "pair", "dex_id"))
    for token, bps in (("0.01%", 1.0), ("0.05%", 5.0), ("0.3%", 30.0), ("0.30%", 30.0), ("1%", 100.0), ("1.00%", 100.0)):
        if token in text:
            return bps, "POOL_NAME"
    pair = str(pool.get("pair") or "").upper()
    symbols = {p.strip() for p in pair.split("/") if p.strip()}
    stables = {"USDC", "USDT", "DAI", "USDS", "USDG", "FRAX"}
    majors = {"WETH", "ETH", "WBTC", "BTC"}
    if len(symbols & stables) >= 1 and len(symbols & (stables | majors)) >= 2:
        return 5.0, "PAIR_CLASS_ASSUMPTION"
    return 30.0, "PAIR_CLASS_ASSUMPTION"


def _age_days(pool: dict[str, Any]) -> float:
    try:
        created = str(pool.get("pool_created_at") or "")
        if not created:
            return 0.0
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def volume_quality(pool: dict[str, Any]) -> dict[str, Any]:
    """Assess whether one 24h volume print is safe to extrapolate.

    High turnover can be genuine and lucrative, especially for Tactical pools,
    but an extreme volume/TVL ratio or extreme one-day price move must never be
    converted directly into a monthly income promise. This function only adjusts
    *forecast persistence*; it does not claim the observed volume is fake.
    """
    tvl = max(0.0, _f(pool.get("tvl_usd")))
    vol = max(0.0, _f(pool.get("volume_24h_usd")))
    turnover = vol / tvl if tvl > 0 else 0.0
    changes = pool.get("price_change_percentage") or {}
    move24 = abs(_f(changes.get("h24") if isinstance(changes, dict) else 0.0))
    age = _age_days(pool)
    factor = 1.0
    flags: list[str] = []
    if turnover > 25:
        factor *= 0.12; flags.append(f"EXTREME_TURNOVER_{turnover:.1f}X_TVL")
    elif turnover > 12:
        factor *= 0.25; flags.append(f"VERY_HIGH_TURNOVER_{turnover:.1f}X_TVL")
    elif turnover > 6:
        factor *= 0.45; flags.append(f"HIGH_TURNOVER_{turnover:.1f}X_TVL")
    elif turnover > 3:
        factor *= 0.70; flags.append(f"ELEVATED_TURNOVER_{turnover:.1f}X_TVL")
    if move24 > 500:
        factor *= 0.08; flags.append(f"EXTREME_24H_PRICE_MOVE_{move24:.0f}PCT")
    elif move24 > 100:
        factor *= 0.20; flags.append(f"VERY_HIGH_24H_PRICE_MOVE_{move24:.0f}PCT")
    elif move24 > 35:
        factor *= 0.55; flags.append(f"HIGH_24H_PRICE_MOVE_{move24:.0f}PCT")
    if 0 < age < 2:
        factor *= 0.55; flags.append("POOL_UNDER_2_DAYS_OLD")
    elif 0 < age < 7:
        factor *= 0.75; flags.append("POOL_UNDER_7_DAYS_OLD")
    return {
        "factor": max(0.02, min(1.0, factor)),
        "turnover_24h_vs_tvl": turnover,
        "price_move_24h_pct": move24,
        "pool_age_days": age,
        "flags": flags,
        "quality": "LOW" if factor < 0.35 else "MODERATE" if factor < 0.75 else "GOOD",
    }


def estimate_lp_economics(
    pool: dict[str, Any],
    *,
    capital: float,
    active_time_pct: float,
    width_pct: float,
    regime: dict[str, Any] | None = None,
    expected_interventions_per_month: float = 0.0,
    lifecycle_cost_per_intervention: float = 1.0,
    lower_price: float | None = None,
    upper_price: float | None = None,
) -> dict[str, Any]:
    """Estimate fee economics without pretending modelled IL is a cash expense.

    The output intentionally separates:
      * fee income / operating net (fees minus execution friction), from
      * LP-vs-HODL divergence risk (a planning allowance, not booked P&L).

    When current V3 active liquidity and an explicit range are available, fee
    share is estimated from current active liquidity rather than capital/TVL.
    """
    capital = max(0.0, _f(capital))
    tvl = max(0.0, _f(pool.get("tvl_usd")))
    vol24 = max(0.0, _f(pool.get("volume_24h_usd")))
    active = max(0.0, min(100.0, _f(active_time_pct))) / 100.0
    width = max(1.0, _f(width_pct, 25.0))
    fee_bps, fee_source = infer_fee_tier_bps(pool)
    fee_rate = fee_bps / 10_000.0

    if capital <= 0 or tvl <= 0 or vol24 <= 0 or fee_rate <= 0:
        return {
            "mode": "INSUFFICIENT_DATA", "capital_usd": capital, "estimated": True,
            "reason": "Capital, TVL, volume and fee tier are required for fee economics.",
        }

    quality = volume_quality(pool)
    observed_pool_fees_day = vol24 * fee_rate
    effective_volume = vol24 * quality["factor"]
    effective_pool_fees_day = effective_volume * fee_rate

    share_method = "TVL_RANGE_PROXY"
    share = 0.0
    share_detail = None
    if lower_price and upper_price and upper_price > lower_price:
        share_detail = active_liquidity_share_for_capital(
            pool, capital_usd=capital, lower=float(lower_price), upper=float(upper_price)
        )
    if share_detail:
        share = _f(share_detail.get("share"))
        concentration = None
        share_method = str(share_detail.get("method") or "CURRENT_ACTIVE_LIQUIDITY_PROXY")
    else:
        concentration = max(0.65, min(2.0, 28.0 / width))
        share = (capital / tvl) * concentration

    # Current 24h fee opportunity. quality.factor suppresses *forecasting* of
    # abnormal activity but leaves observed raw fee flow visible to the user.
    gross_position_day = effective_pool_fees_day * max(0.0, min(1.0, share)) * active

    regime = regime or {}
    vol_pct = abs(_f(regime.get("realised_volatility_pct")))
    trend_score = abs(_f(regime.get("score"), 50.0) - 50.0)
    breakout = str(regime.get("breakout_state") or "")
    regime_fee_factor = 1.0
    if vol_pct > 6:
        regime_fee_factor *= max(0.78, 1.0 - min(0.18, (vol_pct - 6.0) * 0.018))
    if "BREAKOUT" in breakout or "BREAKDOWN" in breakout:
        regime_fee_factor *= 0.94

    base_day = gross_position_day * regime_fee_factor
    raw_month = base_day * 30.4375

    # Persistence reflects evidence quality, not risk aversion. Strategy Lab has
    # multi-day candles so it is allowed to retain more of the current run-rate.
    age = quality["pool_age_days"]
    persistence = 0.48 if age < 7 else 0.60 if age < 30 else 0.72
    if fee_source == "PAIR_CLASS_ASSUMPTION":
        persistence *= 0.80
    persistence *= max(0.20, quality["factor"] ** 0.45)
    if share_method == "CURRENT_ACTIVE_LIQUIDITY_PROXY":
        persistence = min(0.90, persistence * 1.12)

    decision_gross_month = raw_month * persistence
    friction_month = max(0.0, expected_interventions_per_month) * max(0.0, lifecycle_cost_per_intervention)
    operating_net = decision_gross_month - friction_month

    # This is NOT a predicted cash loss. It is a planning allowance expressing
    # LP-vs-HODL divergence / directional inventory risk so the allocator can
    # compare two otherwise similar opportunities.
    divergence_risk_pct = min(8.0, max(0.0, vol_pct * 0.12 + trend_score * 0.020))
    divergence_risk = capital * divergence_risk_pct / 100.0
    risk_adjusted_planning = operating_net - divergence_risk

    gross_apr = base_day * 365.0 / capital * 100.0
    operating_month_pct = operating_net / capital * 100.0
    low = max(-friction_month, decision_gross_month * 0.55 - friction_month)
    high = decision_gross_month * 1.35 - friction_month

    confidence = "MODERATE"
    if quality["factor"] < 0.35 or fee_source == "PAIR_CLASS_ASSUMPTION":
        confidence = "LOW"
    elif share_method == "CURRENT_ACTIVE_LIQUIDITY_PROXY" and quality["factor"] >= 0.75:
        confidence = "MODERATE_TO_HIGH"

    return {
        "mode": "ESTIMATED_FROM_ACTIVE_LIQUIDITY" if share_detail else "ESTIMATED_FROM_VOLUME_TVL_RANGE",
        "estimated": True,
        "capital_usd": round(capital, 2),
        "fee_tier_bps": round(fee_bps, 3),
        "fee_tier_source": fee_source,
        "observed_pool_fee_flow_day_usd": round(observed_pool_fees_day, 2),
        "effective_volume_24h_usd": round(effective_volume, 2),
        "volume_quality": quality,
        "fee_share_method": share_method,
        "liquidity_share_baseline_pct": round(max(0.0, min(1.0, share)) * 100.0, 8),
        "active_liquidity_detail": share_detail,
        "concentration_factor": round(concentration, 3) if concentration is not None else None,
        "active_time_pct": round(active * 100.0, 2),
        "regime_fee_factor": round(regime_fee_factor, 3),
        "estimated_fee_income": {
            "daily": round(base_day, 2),
            "weekly": round(base_day * 7.0, 2),
            "monthly": round(decision_gross_month, 2),
            "raw_24h_run_rate_monthly": round(raw_month, 2),
            "monthly_low": round(low, 2),
            "monthly_high": round(high, 2),
        },
        "gross_apr_pct": round(gross_apr, 1),
        "estimated_lifecycle_cost_month_usd": round(friction_month, 2),
        "estimated_operating_net_month_usd": round(operating_net, 2),
        # Compatibility: from v0.8.1 'net' means fee operating net, not a
        # fabricated IL cash deduction.
        "estimated_net_month_usd": round(operating_net, 2),
        "estimated_net_month_pct": round(operating_month_pct, 2),
        "estimated_net_month_range_usd": [round(low, 2), round(high, 2)],
        "lp_vs_hodl_risk_allowance_month_usd": round(divergence_risk, 2),
        "lp_vs_hodl_risk_allowance_pct": round(divergence_risk_pct, 2),
        "risk_adjusted_planning_month_usd": round(risk_adjusted_planning, 2),
        # legacy aliases retained so old clients/tests don't break
        "estimated_il_regime_drag_month_usd": round(divergence_risk, 2),
        "raw_24h_run_rate_month_usd": round(raw_month - friction_month, 2),
        "persistence_haircut_factor": round(persistence, 3),
        "confidence": confidence,
        "assumptions": [
            "Fee income is an estimate, not guaranteed return.",
            "LP-vs-HODL divergence risk is shown separately and is not booked as a monthly cash expense.",
            "Extreme one-day volume/price anomalies are haircut before extrapolation.",
            "Current active V3 liquidity is used when available; otherwise TVL/range is a fallback proxy.",
            "Exact realised economics require actual fee-growth, inventory marks, gas and exit execution.",
        ],
    }


def estimate_replay_economics(
    candles: list[dict[str, Any]],
    activities: list[float],
    pool: dict[str, Any],
    *,
    capital: float,
    width_pct: float,
    regime: dict[str, Any] | None = None,
    interventions: int = 0,
) -> dict[str, Any]:
    capital = max(0.0, _f(capital)); tvl = max(0.0, _f(pool.get("tvl_usd")))
    if not candles or not activities or capital <= 0 or tvl <= 0:
        return {"mode":"INSUFFICIENT_DATA","estimated":True,"reason":"Historical volume plus current TVL proxy are required."}
    fee_bps, fee_source = infer_fee_tier_bps(pool); fee_rate = fee_bps / 10000.0
    captured = sum(max(0.0,_f(c.get("volume"))) * max(0.0,min(1.0,a)) for c,a in zip(candles,activities))
    total_volume = sum(max(0.0,_f(c.get("volume"))) for c in candles)
    stamps = [_f(c.get("timestamp")) for c in candles if _f(c.get("timestamp")) > 0]
    days = max(1/24,(max(stamps)-min(stamps))/86400.0) if len(stamps)>=2 else max(1.0,len(candles)/24.0)
    avg_captured_volume_day = captured / days
    width = max(1.0,_f(width_pct,25.0)); concentration = max(0.65,min(2.0,28.0/width))
    share = capital / tvl
    fees_total = avg_captured_volume_day * fee_rate * share * concentration * days
    fees_day = fees_total / days
    regime = regime or {}; vol_pct = abs(_f(regime.get("realised_volatility_pct"))); trend = abs(_f(regime.get("score"),50.0)-50.0)
    divergence_pct_month = min(10.0,max(0.0,vol_pct*0.12+trend*0.020))
    divergence_replay = capital * divergence_pct_month / 100.0 * (days/30.4375)
    friction = max(0,interventions) * 1.5
    operating_net = fees_total - friction
    risk_adjusted = operating_net - divergence_replay
    start=_f(candles[0].get("close")); end=_f(candles[-1].get("close")); price_move=(end/start-1)*100 if start>0 else 0.0
    operating_month = fees_day*30.4375 - (friction/max(days,1e-9)*30.4375)
    risk_month = capital * divergence_pct_month / 100.0
    return {
        "mode":"ESTIMATED_HISTORICAL_VOLUME_CURRENT_TVL_PROXY",
        "estimated":True,
        "replay_days":round(days,2),
        "fee_tier_bps":round(fee_bps,3),"fee_tier_source":fee_source,
        "historical_pool_volume_usd":round(total_volume,2),
        "historical_in_range_volume_usd":round(captured,2),
        "average_in_range_volume_day_usd":round(avg_captured_volume_day,2),
        "estimated_fees_total_usd":round(fees_total,2),
        "estimated_fees_day_usd":round(fees_day,2),
        "estimated_fees_month_run_rate_usd":round(fees_day*30.4375,2),
        "estimated_gross_apr_pct":round(fees_day*365/capital*100,1),
        "estimated_intervention_cost_usd":round(friction,2),
        "estimated_operating_net_replay_usd":round(operating_net,2),
        "estimated_net_replay_usd":round(operating_net,2),
        "estimated_net_replay_pct":round(operating_net/capital*100,2),
        "estimated_net_month_run_rate_usd":round(operating_month,2),
        "estimated_operating_net_month_usd":round(operating_month,2),
        "lp_vs_hodl_risk_allowance_replay_usd":round(divergence_replay,2),
        "lp_vs_hodl_risk_allowance_month_usd":round(risk_month,2),
        "risk_adjusted_planning_replay_usd":round(risk_adjusted,2),
        # compatibility alias; explicitly no longer described as realised IL
        "estimated_il_regime_allowance_usd":round(divergence_replay,2),
        "underlying_price_move_pct":round(price_move,2),
        "confidence":"MODERATE" if fee_source!="PAIR_CLASS_ASSUMPTION" else "LOW_TO_MODERATE",
        "assumptions":[
            "Historical pool candle volume is observed; position fee share is estimated.",
            "Current TVL is used as a proxy because historical active tick liquidity is not reconstructed.",
            "LP-vs-HODL divergence risk is shown separately from fee operating net.",
        ],
    }
