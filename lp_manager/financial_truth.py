from __future__ import annotations

import math
from typing import Any

from .portfolio_accounting import position_accounting


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def portfolio_financial_truth(store) -> dict[str, Any]:
    """Evidence-led portfolio wealth and realised-performance ledger.

    This deliberately separates:
      * gross LP P/L (current LP + earned fees - opening capital),
      * realised income / closed outcomes,
      * transaction costs,
      * LP-vs-HODL,
      * forecasts frozen at recommendation time.
    """
    positions=store.list_positions()
    events=store.list_financial_events(2000) if hasattr(store,"list_financial_events") else []
    forecasts=store.list_forecast_snapshots(500) if hasattr(store,"list_forecast_snapshots") else []

    opening_verified=0.0
    current_open_wealth=0.0
    current_open_principal=0.0
    all_time_fees=0.0
    verified_open_count=0
    hodl_total=0.0
    lp_for_hodl=0.0
    hodl_count=0
    realised_open_fee_income=0.0
    closed_reported_pnl=0.0
    closed_reported_count=0
    rows=[]

    for p in positions:
        pid=str(p.get("id") or "")
        snap=store.get_position_snapshot(pid) or {}
        tracker=store.get_setting(f"fees:tracker:{pid}",{}) or {}
        acct=position_accounting(p,snap,tracker)
        tracked=max(
            _f(tracker.get("cumulative_earned_usd")),
            _f(p.get("unclaimed_fees"))+_f(p.get("realised_fees")),
        )
        all_time_fees+=tracked
        status=str(p.get("status") or "").upper()
        if status=="OPEN":
            current_open_principal+=_f(acct.get("current_principal_usd"))
            current_open_wealth+=_f(acct.get("strategy_wealth_usd"))
            event_collections=sum(
                max(0.0,_f(e.get("amount_usd")))
                for e in events
                if str(e.get("position_id") or "")==pid
                and str(e.get("event_type") or "").upper()=="COLLECT_FEES"
                and str(e.get("status") or "").upper()=="CONFIRMED"
            )
            realised_open_fee_income+=max(
                max(0.0,_f(p.get("realised_fees"))),
                max(0.0,_f(tracker.get("collected_lower_bound_usd"))),
                event_collections,
            )
            if acct.get("basis_ready"):
                opening_verified+=_f(acct.get("cost_basis_usd"))
                verified_open_count+=1
            if acct.get("hodl_value_usd") is not None:
                hodl_total+=_f(acct.get("hodl_value_usd"))
                lp_for_hodl+=_f(acct.get("strategy_wealth_usd"))
                hodl_count+=1
        else:
            quality=str(p.get("pnl_quality") or "").upper()
            if quality not in {"","UNKNOWN","FIRST_OBSERVED"}:
                closed_reported_pnl+=_f(p.get("reported_net_pnl"))
                closed_reported_count+=1

        rows.append({
            "position_id":pid,
            "display_name":p.get("display_name") or p.get("pair"),
            "status":status,
            "basis_ready":bool(acct.get("basis_ready")),
            "opening_capital_usd":acct.get("cost_basis_usd") if acct.get("basis_ready") else None,
            "current_lp_value_usd":acct.get("current_principal_usd"),
            "fees_earned_usd":acct.get("fees_earned_usd"),
            "pnl_incl_fees_usd":acct.get("absolute_pnl_incl_fees_usd"),
            "transaction_costs_usd":acct.get("gas_costs_usd"),
            "net_pnl_after_costs_usd":acct.get("net_pnl_after_costs_usd"),
            "hodl_value_usd":acct.get("hodl_value_usd"),
            "lp_vs_hodl_usd":acct.get("lp_vs_hodl_usd"),
        })

    event_gas=sum(_f(x.get("gas_usd")) for x in events if str(x.get("status") or "").upper()=="CONFIRMED")
    recorded_position_gas=sum(max(0.0,_f(p.get("gas_costs"))) for p in positions)
    transaction_costs=event_gas if event_gas>0 else recorded_position_gas
    open_ids={str(p.get("id") or "") for p in positions if str(p.get("status") or "").upper()=="OPEN"}
    open_event_gas_by_position={}
    for x in events:
        pid=str(x.get("position_id") or "")
        if pid not in open_ids or str(x.get("status") or "").upper()!="CONFIRMED":
            continue
        open_event_gas_by_position[pid]=open_event_gas_by_position.get(pid,0.0)+max(0.0,_f(x.get("gas_usd")))
    open_position_gas=0.0
    for p in positions:
        pid=str(p.get("id") or "")
        if pid not in open_ids:
            continue
        event_cost=open_event_gas_by_position.get(pid,0.0)
        # New V0.8.8 executions have receipt-backed events. Older imported/live
        # positions may only have a recorded gas_costs figure, so use that as the
        # fallback rather than pretending historical transaction cost was zero.
        open_position_gas+=event_cost if event_cost>0 else max(0.0,_f(p.get("gas_costs")))

    # Realised G/L is intentionally conservative. Collected fees on still-open
    # positions are realised cash and their best available transaction-cost
    # evidence is deducted. Closed-position P/L from an ONCHAIN_CLOSE_RECEIPT is
    # already net of that position's cumulative recorded costs, so those costs are
    # not subtracted a second time here.
    realised_gain_loss=realised_open_fee_income-open_position_gas+closed_reported_pnl

    gross_open_pnl=current_open_wealth-opening_verified if verified_open_count and opening_verified>0 else None
    lp_vs_hodl=lp_for_hodl-hodl_total if hodl_count else None

    linked=[f for f in forecasts if f.get("position_id")]
    comparisons=[]
    by_position={str(r["position_id"]):r for r in rows}
    for f in linked:
        pos=by_position.get(str(f.get("position_id") or ""))
        if not pos or pos.get("pnl_incl_fees_usd") is None:
            continue
        actual=_f(pos.get("fees_earned_usd"))
        forecast=_f(f.get("expected_fees_usd"))
        error=actual-forecast
        comparisons.append({
            "forecast_id":f.get("id"),"position_id":f.get("position_id"),
            "forecast_fees_usd":round(forecast,4),"actual_fees_usd":round(actual,4),
            "fee_forecast_error_usd":round(error,4),
            "fee_forecast_error_pct":round(error/forecast*100.0,2) if forecast>0 else None,
            "model_version":f.get("model_version"),
        })

    return {
        "opening_wealth_verified_usd":round(opening_verified,2),
        "current_open_lp_value_usd":round(current_open_principal,2),
        "current_open_strategy_wealth_usd":round(current_open_wealth,2),
        "gross_open_pnl_incl_fees_usd":round(gross_open_pnl,2) if gross_open_pnl is not None else None,
        "all_time_known_fees_usd":round(all_time_fees,2),
        "realised_gain_loss_usd":round(realised_gain_loss,2),
        "realised_open_fee_income_usd":round(realised_open_fee_income,2),
        "closed_reported_pnl_usd":round(closed_reported_pnl,2),
        "transaction_costs_usd":round(transaction_costs,2),
        "lp_vs_hodl_usd":round(lp_vs_hodl,2) if lp_vs_hodl is not None else None,
        "hodl_benchmark_usd":round(hodl_total,2) if hodl_count else None,
        "basis_coverage":{"verified_open_positions":verified_open_count,"open_positions":sum(1 for p in positions if str(p.get("status") or "").upper()=="OPEN")},
        "closed_realised_coverage":{"verified_closed_positions":closed_reported_count,"closed_positions":sum(1 for p in positions if str(p.get("status") or "").upper()=="CLOSED")},
        "forecast_audit":{"snapshots":len(forecasts),"linked_to_positions":len(linked),"comparisons":comparisons[:20]},
        "position_rows":rows,
        "evidence_note":"Realised G/L only includes collected fees on open positions, confirmed transaction costs, and closed-position P/L with explicit evidence. Unknown historical closes are not invented.",
    }
