from __future__ import annotations

import math
from typing import Any

from .analytics import position_economics, range_metrics


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def portfolio_profit_scorecard(store) -> dict[str, Any]:
    positions = [p for p in store.list_positions() if str(p.get("monitoring_class") or "").upper() != "ARCHIVED_SUPERSEDED"]
    open_rows = [p for p in positions if str(p.get("status") or "").upper() == "OPEN"]
    tracked_fees = 0.0; fees24 = 0.0; fees7 = 0.0; fees30 = 0.0
    deployed = 0.0; basis = 0.0; known_basis = 0.0; earning_capital = 0.0
    rows: list[dict[str, Any]] = []
    for p in open_rows:
        pid = str(p.get("id") or "")
        snap = store.get_position_snapshot(pid) or {}
        tracker = store.get_setting(f"fees:tracker:{pid}", {}) or {}
        value = _f(p.get("current_value")); cost = _f(p.get("capital_value"))
        deployed += value; basis += cost
        if str(p.get("cost_basis_quality") or "UNKNOWN").upper() not in {"UNKNOWN", "FIRST_OBSERVED", ""}:
            known_basis += cost
        current_fees = max(_f(tracker.get("cumulative_earned_usd")), _f(p.get("unclaimed_fees")))
        tracked_fees += current_fees
        fees24 += _f(tracker.get("fees_24h_usd"), _f(p.get("fees_today")))
        fees7 += _f(tracker.get("fees_7d_usd"), _f(p.get("fees_7d")))
        fees30 += _f(tracker.get("fees_30d_usd"), _f(p.get("fees_30d")))
        metrics = range_metrics(p)
        if metrics.get("range_state") == "IN_RANGE":
            earning_capital += value
        econ = position_economics(p)
        rows.append({
            "id": pid, "name": p.get("display_name") or p.get("pair"), "pair": p.get("pair"),
            "sleeve": p.get("strategy_sleeve"), "current_value": round(value, 2), "cost_basis": round(cost, 2),
            "cost_basis_quality": p.get("cost_basis_quality"), "range_state": metrics.get("range_state"),
            "tracked_fees": round(current_fees, 2), "fees_24h": round(_f(tracker.get("fees_24h_usd"), _f(p.get("fees_today"))), 2),
            "fees_7d": round(_f(tracker.get("fees_7d_usd"), _f(p.get("fees_7d"))), 2),
            "observed_fee_apr_pct": round(_f(tracker.get("annualised_fee_pace_pct"), _f(p.get("apr_current"))), 1),
            "net_profit": round(_f(econ.get("net_profit")), 2), "net_return_pct": round(_f(econ.get("net_return_pct")), 2),
        })
    rows.sort(key=lambda r: r["fees_24h"], reverse=True)
    fee_run_rate_month = fees24 * 30.4375
    total_net_known = sum(r["net_profit"] for r in rows)
    return {
        "open_positions": len(open_rows), "deployed_value": round(deployed, 2), "recorded_cost_basis": round(basis, 2),
        "cost_basis_with_strong_evidence": round(known_basis, 2), "tracked_fees_since_open": round(tracked_fees, 2),
        "fees_24h": round(fees24, 2), "fees_7d": round(fees7, 2), "fees_30d": round(fees30, 2),
        "fee_run_rate_month": round(fee_run_rate_month, 2),
        "fee_run_rate_month_pct_on_deployed": round(fee_run_rate_month / deployed * 100.0, 2) if deployed > 0 else 0.0,
        "capital_currently_earning_pct": round(earning_capital / deployed * 100.0, 1) if deployed > 0 else 0.0,
        "recorded_net_profit": round(total_net_known, 2),
        "data_quality": {
            "cost_basis_coverage_pct": round(known_basis / basis * 100.0, 1) if basis > 0 else 0.0,
            "fee_tracker_positions": sum(1 for p in open_rows if store.get_setting(f"fees:tracker:{p.get('id')}", {})),
        },
        "positions": rows,
        "best_fee_producer": rows[0] if rows else None,
        "note": "Fee run-rate is observational and can change quickly. Recorded P/L is only as strong as each position's cost-basis evidence.",
    }
