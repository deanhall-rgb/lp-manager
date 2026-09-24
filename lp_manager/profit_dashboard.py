from __future__ import annotations

import math
from typing import Any

from .analytics import range_metrics
from .portfolio_accounting import position_accounting
from .fee_metrics import observed_fee_metrics


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def portfolio_profit_scorecard(store) -> dict[str, Any]:
    """Portfolio accounting and fee production are deliberately separate."""
    positions=[p for p in store.list_positions() if str(p.get("monitoring_class") or "").upper()!="ARCHIVED_SUPERSEDED"]
    open_rows=[p for p in positions if str(p.get("status") or "").upper()=="OPEN"]
    tracked_fees=fees24=fees7=fees30=mature_fee24=0.0
    deployed=basis=known_basis=earning_capital=0.0
    absolute_pnl=unrealised_principal_pnl=realised_fee_floor=0.0
    hodl_total=lp_total_for_hodl=0.0
    hodl_rows=0
    rows=[]

    for p in open_rows:
        pid=str(p.get("id") or "")
        snap=store.get_position_snapshot(pid) or {}
        tracker=store.get_setting(f"fees:tracker:{pid}",{}) or {}
        value=_f(p.get("current_value"))
        cost=_f(p.get("capital_value"))
        deployed+=value
        basis+=cost
        if str(p.get("cost_basis_quality") or "UNKNOWN").upper() not in {"UNKNOWN","FIRST_OBSERVED",""}:
            known_basis+=cost

        current_fees=max(_f(tracker.get("cumulative_earned_usd")),_f(p.get("unclaimed_fees"))+_f(p.get("realised_fees")))
        f24=_f(tracker.get("fees_24h_usd"),_f(p.get("fees_today")))
        f7=_f(tracker.get("fees_7d_usd"),_f(p.get("fees_7d")))
        f30=_f(tracker.get("fees_30d_usd"),_f(p.get("fees_30d")))
        tracked_fees+=current_fees
        fees24+=f24
        fees7+=f7
        fees30+=f30
        if _f(tracker.get("age_days"))>=1.0:
            mature_fee24+=f24

        metrics=range_metrics(p)
        if metrics.get("range_state")=="IN_RANGE":
            earning_capital+=value

        accounting=position_accounting(p,snap,tracker)
        observed=observed_fee_metrics(tracker,cost or value)
        if cost>0:
            absolute_pnl+=_f(accounting.get("absolute_pnl_incl_fees_usd"))
            unrealised_principal_pnl+=value-cost
        realised_fee_floor+=max(0.0,_f(p.get("realised_fees")))-max(0.0,_f(p.get("gas_costs")))
        if accounting.get("hodl_value_usd") is not None:
            hodl_rows+=1
            hodl_total+=_f(accounting.get("hodl_value_usd"))
            lp_total_for_hodl+=_f(accounting.get("lp_plus_fees_usd"))

        rows.append({
            "id":pid,
            "name":p.get("display_name") or p.get("pair"),
            "pair":p.get("pair"),
            "sleeve":p.get("strategy_sleeve"),
            "current_value":round(value,2),
            "cost_basis":round(cost,2),
            "cost_basis_quality":p.get("cost_basis_quality"),
            "range_state":metrics.get("range_state"),
            "tracked_fees":round(current_fees,2),
            "fees_24h":round(f24,2),
            "fees_7d":round(f7,2),
            # Legacy field retained, now safe for UI display.
            "observed_fee_apr_pct":observed.get("since_open_annualised_fee_apr_pct"),
            "observed_fee_metrics":observed,
            "accounting":accounting,
            "net_profit":round(_f(accounting.get("absolute_pnl_incl_fees_usd")),2),
            "net_return_pct":round(_f(accounting.get("absolute_return_incl_fees_pct")),2),
            "lp_vs_hodl_usd":accounting.get("lp_vs_hodl_usd"),
            "lp_vs_hodl_pct":accounting.get("lp_vs_hodl_pct"),
        })

    rows.sort(key=lambda r:r["fees_24h"],reverse=True)
    fee_run_rate_month=mature_fee24*30.4375
    lp_vs_hodl=lp_total_for_hodl-hodl_total if hodl_rows else None
    return {
        "open_positions":len(open_rows),
        "deployed_value":round(deployed,2),
        "recorded_cost_basis":round(basis,2),
        "cost_basis_with_strong_evidence":round(known_basis,2),
        "portfolio_fee_income_since_open":round(tracked_fees,2),
        "tracked_fees_since_open":round(tracked_fees,2),
        "fees_24h":round(fees24,2),
        "fees_7d":round(fees7,2),
        "fees_30d":round(fees30,2),
        "fee_run_rate_month":round(fee_run_rate_month,2),
        "fee_run_rate_month_pct_on_deployed":round(fee_run_rate_month/deployed*100.0,2) if deployed>0 else 0.0,
        "fee_run_rate_basis":"MATURE_24H_ONLY",
        "capital_currently_earning_pct":round(earning_capital/deployed*100.0,1) if deployed>0 else 0.0,
        "position_pnl_incl_fees":round(absolute_pnl,2),
        "recorded_net_profit":round(absolute_pnl,2),
        "unrealised_principal_pnl":round(unrealised_principal_pnl,2),
        "realised_fee_pnl_floor":round(realised_fee_floor,2),
        "lp_vs_hodl_usd":round(lp_vs_hodl,2) if lp_vs_hodl is not None else None,
        "hodl_benchmark_value_usd":round(hodl_total,2) if hodl_rows else None,
        "lp_value_for_hodl_comparison_usd":round(lp_total_for_hodl,2) if hodl_rows else None,
        "data_quality":{
            "cost_basis_coverage_pct":round(known_basis/basis*100.0,1) if basis>0 else 0.0,
            "fee_tracker_positions":sum(1 for p in open_rows if store.get_setting(f"fees:tracker:{p.get('id')}",{})),
            "mature_24h_fee_positions":sum(1 for p in open_rows if _f((store.get_setting(f"fees:tracker:{p.get('id')}",{}) or {}).get("age_days"))>=1.0),
            "hodl_benchmark_positions":hodl_rows,
        },
        "positions":rows,
        "best_fee_producer":rows[0] if rows else None,
        "note":"Fee production, absolute P/L and LP-vs-HODL are separate. Monthly fee run-rate excludes positions with less than 24 hours of observations.",
    }
