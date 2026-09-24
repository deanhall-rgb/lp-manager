from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import Store
from .models import Position
from .price_units import display_lens, tick_token1_per_token0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        v=float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _load(path: Path) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception: return None


def _ts(*values: Any) -> float:
    for value in values:
        v=_f(value)
        if v>0: return v
        text=str(value or "").strip()
        if text:
            try: return datetime.fromisoformat(text.replace("Z","+00:00")).timestamp()
            except Exception: pass
    return 0.0


def _range_from_snapshot(snap: dict[str, Any], current: dict[str, Any]) -> tuple[float,float,float,str]:
    """Return an explicit, consistent execution-price lens.

    Old campaign ledgers mixed token USD, market-cap and inverse pool ratios. V0.8
    only trusts explicit prices when their unit is explicit; otherwise it derives
    the range from ticks and labels the unit (e.g. DELTA_PER_WETH).
    """
    t0=str(snap.get("token0_symbol") or "TOKEN0")
    t1=str(snap.get("token1_symbol") or "TOKEN1")
    d0=int(snap.get("token0_decimals") or 18); d1=int(snap.get("token1_decimals") or 18)
    tl=snap.get("tick_lower"); tu=snap.get("tick_upper")
    tc=snap.get("current_tick")
    if tc is None: tc=current.get("current_tick")
    if tc is None: tc=snap.get("opening_tick_reconstructed")
    try:
        if tl is not None and tu is not None:
            raw_lo=tick_token1_per_token0(int(tl),d0,d1); raw_hi=tick_token1_per_token0(int(tu),d0,d1)
            raw_cur=tick_token1_per_token0(int(tc),d0,d1) if tc is not None else (raw_lo*raw_hi)**0.5
            lens=display_lens(t0,t1,raw_lo,raw_hi,raw_cur)
            return float(lens["lower"]),float(lens["upper"]),float(lens["current"]),str(lens["unit"])
    except Exception:
        pass
    # Future ledgers may carry explicit unit-labelled ranges. Do not assume naked
    # numbers are USD prices.
    unit=str(snap.get("range_unit") or current.get("range_unit") or "UNKNOWN")
    lower=_f(snap.get("lower_price") or snap.get("price_lower") or snap.get("range_lower") or current.get("lower_price"))
    upper=_f(snap.get("upper_price") or snap.get("price_upper") or snap.get("range_upper") or current.get("upper_price"))
    mark=_f(snap.get("current_price") or snap.get("price") or snap.get("mark_price") or current.get("current_price"))
    if lower>upper: lower,upper=upper,lower
    return lower,upper,mark,unit


def discover_legacy_sources(legacy_root: Path) -> dict[str, Any]:
    files={}
    for name in ("campaign_ledger.json","owned_lp_ledger.json","owned_portfolio_manager.json","stage5_last_proposal.json","bot_status.json"):
        p=legacy_root/name; files[name]={"exists":p.exists(),"path":str(p)}
    return files


def _campaign_rows(payload: dict[str, Any]) -> list[tuple[str,dict[str,Any]]]:
    campaigns=payload.get("campaigns") or {}
    if isinstance(campaigns,list): rows=[(str(i),x) for i,x in enumerate(campaigns) if isinstance(x,dict)]
    elif isinstance(campaigns,dict): rows=[(str(k),v) for k,v in campaigns.items() if isinstance(v,dict)]
    else: rows=[]
    rows.sort(key=lambda x:_ts((x[1].get("entry") or {}).get("time_unix"),x[1].get("created_at_unix"),x[1].get("updated_at_unix")))
    return rows


def import_campaign_ledger(store: Store, legacy_root: Path) -> dict[str, Any]:
    path=legacy_root/"campaign_ledger.json"; payload=_load(path)
    if not isinstance(payload,dict):
        return {"ok":False,"reason":"CAMPAIGN_LEDGER_NOT_FOUND_OR_INVALID","path":str(path),"imported":0,"skipped":0}
    imported=0; skipped=0; delta_index=0; diagnostics=[]
    authoritative_delta = store.get_setting("history:delta_pool", {}) or {}
    has_authoritative_delta = bool(authoritative_delta.get("authoritative_token_ids"))
    for campaign_id,campaign in _campaign_rows(payload):
        snap=campaign.get("position_snapshot") or {}
        entry=campaign.get("entry") or {}
        current=campaign.get("current") or {}
        if not isinstance(snap,dict): snap={}
        if not isinstance(entry,dict): entry={}
        if not isinstance(current,dict): current={}
        # v3 ledger stores symbols/addresses in both position_snapshot and entry.
        token0=str(snap.get("token0_symbol") or entry.get("token0_symbol") or "TOKEN0")
        token1=str(snap.get("token1_symbol") or entry.get("token1_symbol") or "TOKEN1")
        pair=f"{token0}/{token1}"; is_delta="DELTA" in {token0.upper(),token1.upper()}
        if is_delta: delta_index+=1
        lower,upper,mark,range_unit=_range_from_snapshot(snap,current)
        ids=snap.get("ids") or []
        token_id=str(ids[0]) if isinstance(ids,list) and ids else (str(snap.get("token_id")) if snap.get("token_id") is not None else None)
        status_raw=str(campaign.get("status") or snap.get("status") or "OPEN_LP").upper()
        # A legacy ledger is historical evidence, not current ownership truth.
        # Only a current live-chain reconciliation may promote a campaign to OPEN.
        status="CLOSED"
        opened=_ts(entry.get("time_unix"),entry.get("time_utc"),campaign.get("created_at_unix"),campaign.get("created_at")) or time.time()
        closed=_ts(campaign.get("updated_at_unix"),campaign.get("updated_at")) if status=="CLOSED" else 0.0
        capital=_f(entry.get("entry_nav_usd") or entry.get("entry_assets_usd") or campaign.get("entry_value_usd") or campaign.get("capital_usd") or snap.get("principal_usd"))
        value=_f(current.get("total_lp_value_usd") or snap.get("total_usd") or current.get("principal_usd") or snap.get("principal_usd") or capital)
        fees=_f(current.get("unclaimed_fees_usd") or snap.get("fees_usd"))
        reported_pnl=_f(current.get("net_pnl_usd") if current.get("net_pnl_usd") is not None else current.get("absolute_pnl_usd"))
        reported_pct=_f(current.get("net_pnl_pct") if current.get("net_pnl_pct") is not None else current.get("absolute_pnl_pct"))
        pnl_quality=str(current.get("absolute_pnl_status") or entry.get("entry_nav_usd_status") or "LEGACY_LEDGER")
        fee_return=_f(snap.get("fee_return_pct"))
        apr=fee_return*365.0/max(1.0,(time.time()-opened)/86400.0) if fee_return else 0.0
        # Once the supplied on-chain DELTA reconstruction is available it owns
        # LP1/LP2/LP3 identity. Older campaign-ledger DELTA rows remain archived
        # evidence and may not reclaim those names on a later manual sync.
        monitoring_class = "ARCHIVED_SUPERSEDED" if (is_delta and has_authoritative_delta) else "HISTORICAL"
        display=(f"Legacy DELTA campaign {delta_index}" if monitoring_class=="ARCHIVED_SUPERSEDED" else f"DELTA LP{delta_index}") if is_delta else f"{pair} campaign"
        existing=store.find_position_by_token(str(snap.get("network") or snap.get("chain") or "ROBINHOOD_CHAIN"), token_id) if token_id else None
        existing_snap=store.get_position_snapshot(str(existing.get("id"))) if existing else None
        live_authority=bool(existing and str(existing.get("source") or "")=="live_chain" and isinstance(existing_snap,dict) and existing_snap.get("live"))
        if live_authority:
            status=str(existing.get("status") or status); lower=_f(existing.get("lower_price"),lower); upper=_f(existing.get("upper_price"),upper); mark=_f(existing.get("current_price"),mark)
            value=_f(existing.get("current_value"),value); fees=_f(existing.get("unclaimed_fees"),fees); range_unit=str(existing.get("range_unit") or range_unit)
        thesis=str(campaign.get("thesis") or campaign.get("entry_thesis") or (campaign.get("plan") or {}).get("thesis") or "")
        exit_goal=str(campaign.get("exit_goal") or (campaign.get("plan") or {}).get("exit_goal") or "")
        position=Position(
            id=str(existing.get("id")) if live_authority else f"legacy-{campaign_id}",protocol=str(snap.get("protocol") or (campaign.get("origin") or {}).get("protocol") or "UNISWAP_V3"),
            chain=str(snap.get("network") or snap.get("chain") or "ROBINHOOD_CHAIN"),pair=pair,status=status,
            lower_price=lower,upper_price=upper,current_price=mark,capital_value=capital,current_value=value,
            unclaimed_fees=fees if status=="OPEN" else 0.0,fees_today=0.0,fees_7d=0.0,fees_30d=0.0,
            realised_fees=fees if status=="CLOSED" else 0.0,estimated_il=0.0,gas_costs=_f(entry.get("entry_gas_usd")),apr_current=apr,apr_7d=0.0,
            opened_at=opened,token_id=token_id,campaign_id=str(campaign.get("campaign_id") or campaign_id),source="live_chain" if live_authority else "legacy_campaign_ledger",
            notes=f"Imported from campaign ledger schema {campaign.get('schema_version',payload.get('schema_version','?'))}. Legacy status: {status_raw}.",
            display_name=display,campaign_label=f"LP{delta_index}" if is_delta else str(campaign_id),entry_thesis=thesis,exit_goal=exit_goal,
            lifecycle_stage="CLOSED" if status=="CLOSED" else "ACTIVE",cost_basis_quality=str(entry.get("basis_quality") or "LEGACY_LEDGER"),strategy_version="legacy-import-v0.8",
            strategy_sleeve="TACTICAL_CAMPAIGN" if is_delta else str(campaign.get("strategy_sleeve") or "TACTICAL_CAMPAIGN"),
            directional_bias=str(campaign.get("directional_bias") or ("BULLISH" if is_delta else "NEUTRAL")),
            inventory_intent=str(campaign.get("inventory_intent") or ("ACCUMULATE_RISK_ASSET" if is_delta else "BALANCED")),
            monitoring_class=monitoring_class,
            pool_address=str(snap.get("pool_address") or entry.get("pool_address") or ""),range_unit=range_unit,closed_at=closed,
            reported_net_pnl=reported_pnl,reported_net_pnl_pct=reported_pct,pnl_quality=pnl_quality,
        )
        store.upsert_position(position)
        store.save_position_snapshot(position.id,{"legacy_campaign":campaign,"position_snapshot":snap,"entry":entry,"current":current,"range_context":{"unit":range_unit,"human_price_asset":snap.get("human_price_asset"),"human_quote_asset":snap.get("human_quote_asset")}})
        imported+=1
        diagnostics.append({"campaign_id":campaign_id,"display_name":display,"status":status,"range_ready":bool(lower>0 and upper>lower and mark>0),"pnl_quality":pnl_quality})
    skipped=max(0,len(_campaign_rows(payload))-imported)
    return {"ok":True,"path":str(path),"imported":imported,"skipped":skipped,"diagnostics":diagnostics,"schema_version":payload.get("schema_version")}
