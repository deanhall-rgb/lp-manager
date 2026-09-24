from __future__ import annotations

import math
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def observation_confidence(age_days: float) -> str:
    age = max(0.0, _f(age_days))
    if age < 1.0:
        return "INSUFFICIENT"
    if age < 3.0:
        return "LOW"
    if age < 7.0:
        return "MODERATE"
    return "HIGH"


def pool_fee_revenue_rate(volume_24h_usd: float, fee_tier_bps: float) -> dict[str, float]:
    """Nominal pool LP-fee flow before position-share and range activity.

    The protocol may divert a protocol fee on specific pools. When that setting is
    not available, callers must label this as nominal rather than realised LP fees.
    """
    volume = max(0.0, _f(volume_24h_usd))
    bps = max(0.0, _f(fee_tier_bps))
    return {
        "volume_24h_usd": volume,
        "fee_tier_bps": bps,
        "nominal_pool_fee_revenue_24h_usd": volume * bps / 10_000.0,
    }


def observed_fee_metrics(tracker: dict[str, Any] | None, capital_usd: float) -> dict[str, Any]:
    tracker = tracker or {}
    capital = max(0.0, _f(capital_usd))
    age = max(0.0, _f(tracker.get("age_days")))
    cumulative = max(0.0, _f(tracker.get("cumulative_earned_usd")))
    fee24 = max(0.0, _f(tracker.get("fees_24h_usd")))
    since_open_return = cumulative / capital * 100.0 if capital > 0 else 0.0

    # Do not present a partial-day sample as a meaningful annualised APR. Keep the
    # raw tracker available for diagnostics, but suppress the display metric.
    spot_1d = fee24 / capital * 365.0 * 100.0 if capital > 0 and age >= 1.0 else None
    since_open_apr = (
        cumulative / capital / age * 365.0 * 100.0
        if capital > 0 and age >= 1.0
        else None
    )
    confidence = observation_confidence(age)
    return {
        "age_days": round(age, 4),
        "position_observed_fee_return_pct": round(since_open_return, 4),
        "spot_1d_annualised_fee_apr_pct": round(spot_1d, 2) if spot_1d is not None else None,
        "since_open_annualised_fee_apr_pct": round(since_open_apr, 2) if since_open_apr is not None else None,
        "confidence": confidence,
        "annualisation_suppressed": age < 1.0,
        "warning": (
            "Observed annualised APR is suppressed until at least 24 hours of fee history exists."
            if age < 1.0
            else "Observed APR is backward-looking and can change quickly."
        ),
    }


def forecast_fee_metrics(
    *,
    capital_usd: float,
    horizon_days: float,
    expected_fees_usd: float,
    expected_cash_costs_usd: float,
    explicitly_named_other_return_usd: float = 0.0,
) -> dict[str, Any]:
    """Build the forward-looking fee framework and enforce cash identities."""
    capital = max(0.0, _f(capital_usd))
    horizon = max(1.0 / 24.0, _f(horizon_days, 1.0))
    fees = max(0.0, _f(expected_fees_usd))
    costs = max(0.0, _f(expected_cash_costs_usd))
    other = _f(explicitly_named_other_return_usd)
    net = fees + other - costs

    # Release-blocking invariant from V0.8.6: net fee cash profit may not exceed
    # gross fee income unless another return component is explicitly named.
    if other == 0.0 and net > fees + 1e-9:
        raise ValueError("Financial invariant violated: net fee profit exceeds gross fees")
    if abs((fees + other - costs) - net) > 1e-9:
        raise ValueError("Financial invariant violated: net return identity does not balance")

    fee_horizon_pct = fees / capital * 100.0 if capital > 0 else 0.0
    net_horizon_pct = net / capital * 100.0 if capital > 0 else 0.0
    forecast_apr = fee_horizon_pct / horizon * 365.0 if capital > 0 else 0.0
    return {
        "forecast_fee_apr_pct": round(forecast_apr, 2),
        "forecast_fee_return_pct": round(fee_horizon_pct, 4),
        "net_horizon_return_pct": round(net_horizon_pct, 4),
        "expected_fees_usd": round(fees, 4),
        "expected_cash_costs_usd": round(costs, 4),
        "explicit_other_return_usd": round(other, 4),
        "expected_net_usd": round(net, 4),
        "identity": "NET = FORECAST_FEES + EXPLICIT_OTHER_RETURN - CASH_COSTS",
    }
