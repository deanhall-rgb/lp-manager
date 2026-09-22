from __future__ import annotations

from typing import Any


def performance_targets(
    *,
    capital: float,
    target_monthly_pct: float,
    actual_today: float = 0.0,
    actual_7d: float = 0.0,
    actual_30d: float = 0.0,
    days_in_month: float = 30.4375,
) -> dict[str, Any]:
    """Translate a reference monthly return into day/week/month fee targets.

    Targets are reporting benchmarks only; they must never cause the strategy
    engine to tighten a range or increase risk simply to hit a number.
    """
    capital = max(0.0, float(capital or 0.0))
    target_pct = max(0.0, float(target_monthly_pct or 0.0))
    month_target = capital * target_pct / 100.0
    daily_target = month_target / max(days_in_month, 1.0)
    weekly_target = daily_target * 7.0

    def row(actual: float, target: float, period_days: float) -> dict[str, float | str]:
        actual = float(actual or 0.0)
        attainment = actual / target * 100.0 if target > 0 else 0.0
        run_rate_month = actual / max(period_days, 1e-9) * days_in_month
        return {
            "actual": round(actual, 2),
            "target": round(target, 2),
            "attainment_pct": round(attainment, 2),
            "monthly_run_rate": round(run_rate_month, 2),
            "status": "AHEAD" if target > 0 and attainment >= 100 else "BEHIND" if target > 0 else "NO_TARGET",
        }

    return {
        "capital": round(capital, 2),
        "reference_monthly_target_pct": round(target_pct, 3),
        "today": row(actual_today, daily_target, 1.0),
        "week": row(actual_7d, weekly_target, 7.0),
        "month": row(actual_30d, month_target, days_in_month),
        "guardrail": "REPORTING_ONLY_DO_NOT_INCREASE_RISK_TO_HIT_TARGET",
    }
