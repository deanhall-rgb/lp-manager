from __future__ import annotations

import time
from typing import Any

from .models import Position
from .price_units import display_lens, tick_token1_per_token0


def _f(v: Any, default: float = 0.0) -> float:
    try: return float(v)
    except Exception: return default


def import_delta_pool_history(store, payload: dict[str,Any]) -> dict[str,Any]:
    """Import the research reconstruction as historical evidence, not live truth."""
    meta=payload.get("metadata") or {}; positions=payload.get("positions") or {}
    if not isinstance(meta,dict) or not isinstance(positions,dict):
        return {"ok":False,"reason":"INVALID_DELTA_HISTORY","imported":0}
    t0=meta.get("token0") or {}; t1=meta.get("token1") or {}
    sym0=str(t0.get("symbol") or "TOKEN0"); sym1=str(t1.get("symbol") or "TOKEN1")
    dec0=int(t0.get("decimals") or 18); dec1=int(t1.get("decimals") or 18)
    chain="ROBINHOOD_CHAIN" if int(meta.get("chain_id") or 0)==4663 else str(meta.get("chain") or "UNKNOWN")
    pool=str(meta.get("pool") or "")
    imported=[]
    for label,row in positions.items():
        if not isinstance(row,dict): continue
        token_id=str(row.get("token_id") or "")
        tl=int(row.get("tick_lower") or 0); tu=int(row.get("tick_upper") or 0)
        open_tick=int(row.get("opening_tick_reconstructed") or ((tl+tu)//2))
        raw_lower=tick_token1_per_token0(tl,dec0,dec1); raw_upper=tick_token1_per_token0(tu,dec0,dec1); raw_entry=tick_token1_per_token0(open_tick,dec0,dec1)
        entry_lens=display_lens(sym0,sym1,raw_lower,raw_upper,raw_entry)
        # Historical entry is not "current". Keep the execution range but leave
        # current unknown until a fresh RPC pool read is attached by the API.
        lens=display_lens(sym0,sym1,raw_lower,raw_upper,0.0)
        lens["entry"] = float(entry_lens["current"])
        lens["entry_label"] = "historical entry"
        opened=_f(row.get("open_timestamp"),time.time()); closed=_f(row.get("close_timestamp"))
        # A historical file saying 'open' means open at the capture date, not open now.
        # Only a current chain scan is allowed to promote a historical record to OPEN.
        existing=store.find_position_by_token(chain,token_id) if token_id else None
        live_now=bool(existing and str(existing.get("source") or "")=="live_chain")
        status=str(existing.get("status")) if live_now else "CLOSED"
        pid=str(existing.get("id")) if live_now else f"history:{chain}:{token_id or label}"
        initial_weth=_f(row.get("actual_initial_weth")); initial_delta=_f(row.get("actual_initial_delta")); open_ratio=_f(row.get("opening_delta_per_weth_reconstructed"))
        entry_weth_equiv=initial_weth+(initial_delta/open_ratio if open_ratio>0 else 0.0)
        fee_weth=_f(row.get("fee_est_weth_from_non_crossing_active_swaps"))
        fin=row.get("financial_evidence") or {}
        realised=bool(fin.get("realised"))
        capital_usd=_f(fin.get("initial_value_usd"))
        realised_fees_usd=_f(fin.get("fee_value_usd")) if realised else 0.0
        reported_pnl=_f(fin.get("absolute_profit_usd")) if realised else 0.0
        reported_pct=_f(fin.get("absolute_return_pct")) if realised else 0.0
        pnl_quality=str(fin.get("quality") or "FEE_RECONSTRUCTION_ONLY")
        p=Position(
            id=pid,protocol="UNISWAP_V3",chain=chain,pair=f"{sym0}/{sym1}",status=status,
            lower_price=float(lens["lower"]),upper_price=float(lens["upper"]),current_price=_f((existing or {}).get("current_price")) if live_now else 0.0,
            capital_value=_f((existing or {}).get("capital_value")) if live_now else capital_usd,current_value=_f((existing or {}).get("current_value")) if live_now else 0.0,
            unclaimed_fees=_f((existing or {}).get("unclaimed_fees")) if live_now else 0.0,fees_today=0,fees_7d=0,fees_30d=0,
            realised_fees=_f((existing or {}).get("realised_fees")) if live_now else realised_fees_usd,estimated_il=0,gas_costs=0,apr_current=0,apr_7d=0,
            opened_at=opened,token_id=token_id or None,campaign_id=f"DELTA_HISTORY_{label}",
            source="live_chain" if live_now else "historical_pool_reconstruction",
            notes=f"Imported from DELTA pool-history reconstruction. Snapshot status was {row.get('status')}; current status requires chain reconciliation.",
            strategy_sleeve="TACTICAL_CAMPAIGN",directional_bias="BULLISH",inventory_intent="BALANCED",target_hold_days=max(0.1,_f(row.get("elapsed_seconds"))/86400),monitoring_class="HISTORICAL",
            display_name=f"{str(label).upper()} - {sym0}/{sym1}",campaign_label=str(label).upper(),entry_thesis="Historical DELTA/WETH tactical LP evidence",exit_goal="Historical outcome evidence",
            lifecycle_stage="ACTIVE" if status=="OPEN" else "CLOSED",cost_basis_quality="TOKEN_AMOUNTS_RECONSTRUCTED",strategy_version="historical-import-v0.8",
            pool_address=pool,range_unit=str(lens["unit"]),closed_at=closed,
            reported_net_pnl=reported_pnl,reported_net_pnl_pct=reported_pct,pnl_quality=pnl_quality,
        )
        store.upsert_position(p)
        evidence={
            "historical_pool_reconstruction":True,"snapshot_as_of":meta.get("latest_chain_time_at_run_utc"),"source_status":row.get("status"),
            "pool_address":pool,"fee_pips":row.get("fee_pips") or meta.get("fee_pips"),"token0":t0,"token1":t1,
            "price_lens":lens,"entry_price":float(entry_lens["current"]),"entry_price_unit":str(entry_lens["unit"]),"entry_amounts":{"weth":initial_weth,"delta":initial_delta,"entry_weth_equivalent":entry_weth_equiv},
            "observed":{"range_utilisation_pct":_f(row.get("range_utilisation_pct")),"active_hours":_f(row.get("active_hours")),"inactive_hours":_f(row.get("inactive_hours")),"exit_count":int(row.get("exit_count") or 0),"reentry_count":int(row.get("reentry_count") or 0),"longest_inactive_hours":_f(row.get("longest_inactive_hours")),"swap_count":int(row.get("swap_count_during_lifetime") or 0),"active_volume_weth_lower_bound":_f(row.get("fully_active_volume_weth_lower_bound"))},
            "fee_evidence":{"reconstructed_weth_lower_bound":fee_weth,"method":"NON_CROSSING_FULLY_ACTIVE_SWAPS","quality":"RECONSTRUCTED_LOWER_BOUND","note":"Boundary-crossing fee allocation is excluded; exact fee-claim evidence is shown separately when available."},
            "financial_evidence":fin,
            "raw_position_summary":row,
        }
        if live_now:
            live_snap=store.get_position_snapshot(pid) or {}
            live_snap["historical_evidence"]=evidence
            store.save_position_snapshot(pid,live_snap)
        else:
            store.save_position_snapshot(pid,evidence)
        imported.append({"id":pid,"label":label,"token_id":token_id,"status":status,"range_unit":lens["unit"],"range":[lens["lower"],lens["upper"]],"fee_est_weth":fee_weth,"range_utilisation_pct":_f(row.get("range_utilisation_pct"))})
    authoritative={str(x.get("token_id") or "") for x in imported if x.get("token_id")}
    archived=store.archive_superseded_legacy_delta(authoritative)
    store.set_setting("history:delta_pool",{"imported_at":time.time(),"pool":pool,"positions":len(imported),"generated_at":meta.get("generated_at_utc"),"archived_legacy_rows":archived,"authoritative_token_ids":sorted(authoritative)})
    return {"ok":True,"imported":len(imported),"positions":imported,"pool":pool,"chain":chain,"archived_legacy_rows":archived}
