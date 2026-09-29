from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

from .campaign_accounting import build_campaigns
from .fx import money_context


FUNDING_TYPES={"EXTERNAL_FUNDING","LEGACY_BROUGHT_FORWARD"}
WITHDRAWAL_TYPES={"EXTERNAL_WITHDRAWAL"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _ts(value: Any) -> float:
    if isinstance(value,(int,float)):
        return float(value)
    raw=str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z","+00:00")).timestamp()
    except Exception:
        return 0.0


def _norm_addr(value: Any) -> str:
    return str(value or "").strip().lower()


def _capital_current_gbp(settings, store) -> float:
    money=money_context(settings,store)
    rate=_f(money.get("usd_to_display_rate"),1.0)
    snapshot=store.get_wallet_snapshot() or {}
    total_usd=_f(snapshot.get("total_tracked_value_usd"))
    total_usd+=sum(
        max(0.0,_f(p.get("unclaimed_fees")))
        for p in store.list_positions("OPEN")
        if str(p.get("monitoring_class") or "").upper()!="ARCHIVED_SUPERSEDED"
        and str(p.get("source") or "")!="legacy_campaign_ledger"
    )
    return total_usd*rate


def reconcile_reviewed_audit(store, rules: dict[str,Any]) -> dict[str,Any]:
    funding_transactions={
        str(k).lower():dict(v or {})
        for k,v in dict(rules.get("funding_transactions") or {}).items()
    }
    internal_addresses={_norm_addr(x) for x in (rules.get("internal_addresses") or []) if _norm_addr(x)}
    ignore_assets={str(x or "").lower() for x in (rules.get("ignore_assets") or [])}
    ignore_transactions={str(x or "").lower() for x in (rules.get("ignore_transactions") or [])}
    explicit_internal_transactions={str(x or "").lower() for x in (rules.get("internal_transactions") or [])}

    tx_rows=store.list_wallet_audit_transactions(5000)
    tx_by_hash={
        (str(x.get("chain") or ""),str(x.get("tx_hash") or "").lower()):x
        for x in tx_rows
    }
    protocol_counterparties={
        _norm_addr(x.get("to_address"))
        for x in tx_rows
        if str(x.get("kind") or "").upper()=="CONTRACT_CALL"
        and str(x.get("direction") or "").upper() in {"OUT","SELF"}
        and _norm_addr(x.get("to_address"))
    }

    counts={"funding":0,"internal_transfer":0,"internal_protocol":0,"ignored":0,"unchanged":0}
    for row in store.list_wallet_audit_events(5000):
        if str(row.get("review_status") or "").upper()!="UNRESOLVED":
            counts["unchanged"]+=1
            continue
        tx_hash=str(row.get("tx_hash") or "").lower()
        chain=str(row.get("chain") or "")
        frm=_norm_addr(row.get("from_address"))
        to=_norm_addr(row.get("to_address"))
        asset=str(row.get("asset") or "").lower()
        tx=tx_by_hash.get((chain,tx_hash)) or {}
        kind=str(tx.get("kind") or "").upper()
        tx_direction=str(tx.get("direction") or "").upper()

        status=""
        fiat_amount=None
        note=""

        if tx_hash in funding_transactions:
            cfg=funding_transactions[tx_hash]
            status="CONFIRMED_FUNDING"
            fiat_amount=max(0.0,_f(cfg.get("amount_gbp")))
            note=str(cfg.get("note") or "Reviewed cash funding")
            counts["funding"]+=1
        elif tx_hash in ignore_transactions or asset in ignore_assets:
            status="IGNORE"
            note="Reviewed historical noise / unsolicited token"
            counts["ignored"]+=1
        elif tx_hash in explicit_internal_transactions or frm in internal_addresses or to in internal_addresses or kind=="BRIDGE":
            status="INTERNAL_TRANSFER"
            note="Reviewed internal wallet / bridge movement"
            counts["internal_transfer"]+=1
        elif kind=="CONTRACT_CALL" and tx_direction in {"OUT","SELF"}:
            status="INTERNAL_PROTOCOL"
            note="Reviewed wallet-initiated protocol transaction"
            counts["internal_protocol"]+=1
        elif frm in protocol_counterparties or to in protocol_counterparties:
            status="INTERNAL_PROTOCOL"
            note="Reviewed protocol counterparty movement"
            counts["internal_protocol"]+=1

        if status:
            store.resolve_wallet_audit_event(
                str(row.get("id") or ""),
                review_status=status,
                fiat_amount=fiat_amount,
                fiat_currency="GBP",
                note=note,
            )
        else:
            counts["unchanged"]+=1
    return counts


def import_reviewed_baseline(store, payload: dict[str,Any]) -> dict[str,Any]:
    currency=str(payload.get("currency") or "GBP").upper()
    if currency!="GBP":
        raise ValueError("The reviewed investor baseline currently expects GBP")
    entries=list(payload.get("entries") or [])
    if not entries:
        raise ValueError("No capital-ledger entries supplied")

    if bool(payload.get("replace",True)):
        store.clear_capital_ledger("REVIEWED_BASELINE")

    imported=[]
    for raw in entries:
        event_type=str(raw.get("event_type") or "").upper()
        if event_type not in FUNDING_TYPES|WITHDRAWAL_TYPES|{"ADJUSTMENT"}:
            raise ValueError(f"Unsupported capital-ledger event type: {event_type}")
        row={
            **dict(raw),
            "occurred_at":_ts(raw.get("occurred_at")),
            "event_type":event_type,
            "source":str(raw.get("source") or "REVIEWED_BASELINE_V095"),
        }
        if not str(row.get("id") or ""):
            raise ValueError("Every reviewed baseline entry requires a stable id")
        imported.append(store.upsert_capital_ledger_entry(row))

    reconciliation=reconcile_reviewed_audit(store,dict(payload.get("audit_rules") or {}))
    meta={
        "installed_at":time.time(),
        "version":str(payload.get("version") or "v0.9.5"),
        "currency":"GBP",
        "entry_count":len(imported),
        "reviewed_by":str(payload.get("reviewed_by") or "operator"),
        "note":str(payload.get("note") or ""),
        "reconciliation":reconciliation,
    }
    store.set_setting("capital_ledger:baseline_meta",meta)
    return {"ok":True,"entries":imported,"meta":meta}


def _canonical_audit_moves(store) -> dict[tuple[str,str],list[dict[str,Any]]]:
    grouped: dict[tuple[str,str],list[dict[str,Any]]]={}
    raw=store.list_wallet_audit_events(10000)
    by_tx: dict[tuple[str,str],list[dict[str,Any]]]={}
    for row in raw:
        if str(row.get("review_status") or "").upper()=="IGNORE":
            continue
        key=(str(row.get("chain") or "").upper(),str(row.get("tx_hash") or "").lower())
        if not key[1]:
            continue
        by_tx.setdefault(key,[]).append(dict(row))

    for key,rows in by_tx.items():
        named=[x for x in rows if str(x.get("asset") or "").upper()!="TOKEN"]
        out=[]
        seen=set()
        for row in rows:
            asset=str(row.get("asset") or "").upper()
            amount=max(0.0,_f(row.get("amount")))
            direction=str(row.get("direction") or "").upper()
            if asset=="TOKEN":
                # Robinscan can emit a generic TOKEN copy beside the decoded symbol.
                # It is evidence, not another economic movement.
                if any(
                    str(x.get("direction") or "").upper()==direction
                    and abs(_f(x.get("amount"))-amount)<=max(1e-12,abs(amount)*1e-8)
                    for x in named
                ):
                    continue
                # Unresolved generic token rows are not safe for cost-basis attribution.
                continue
            dedupe=(direction,asset,round(amount,12),_norm_addr(row.get("from_address")),_norm_addr(row.get("to_address")))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(row)
        grouped[key]=out
    return grouped


def _legacy_transfer_quantities(store, rows: list[dict[str,Any]]) -> dict[str,float]:
    out: dict[str,float]={}
    for ledger in rows:
        if str(ledger.get("event_type") or "").upper()!="LEGACY_BROUGHT_FORWARD":
            continue
        meta=dict(ledger.get("metadata") or {})
        legacy_wallet=_norm_addr(meta.get("legacy_wallet"))
        if not legacy_wallet:
            continue
        # The reviewed legacy event is timestamped at the first migration transfer.
        # Accept the same migration window through the rest of that day.
        start=_f(ledger.get("occurred_at"))-300
        end=_f(ledger.get("occurred_at"))+24*3600
        for event in store.list_wallet_audit_events(10000):
            if str(event.get("direction") or "").upper()!="IN":
                continue
            if _norm_addr(event.get("from_address"))!=legacy_wallet:
                continue
            when=_f(event.get("occurred_at"))
            if when and (when<start or when>end):
                continue
            symbol=str(event.get("asset") or "").upper()
            if symbol in {"","TOKEN","PLAY","CAT","MUSEBOOK"}:
                continue
            out[symbol]=out.get(symbol,0.0)+max(0.0,_f(event.get("amount")))
    return out


def _transaction_acquisition_basis(settings, store, rows: list[dict[str,Any]]) -> dict[str,dict[str,float]]:
    """Estimate current wallet-asset acquisition basis from reviewed cash + swaps.

    This is deliberately narrower than LP position accounting. Position opening
    capital remains authoritative for capital inside open NFTs. Here we only
    derive the acquisition basis of campaign assets that are presently sitting
    in the wallet, so the campaign view no longer treats wallet inventory as if
    it had zero or today's cost basis.
    """
    funding=[
        x for x in rows
        if str(x.get("event_type") or "").upper()=="EXTERNAL_FUNDING"
        and str(x.get("asset") or "").upper() in {"ETH","WETH"}
        and _f(x.get("asset_amount"))>0
        and _f(x.get("amount_gbp"))>0
    ]
    external_eth_qty=sum(_f(x.get("asset_amount")) for x in funding)
    external_eth_basis=sum(_f(x.get("amount_gbp")) for x in funding)

    legacy_qty=_legacy_transfer_quantities(store,rows)
    legacy_alloc: dict[str,float]={}
    for row in rows:
        if str(row.get("event_type") or "").upper()!="LEGACY_BROUGHT_FORWARD":
            continue
        for symbol,amount in dict((row.get("metadata") or {}).get("allocations_gbp") or {}).items():
            key=str(symbol or "").upper()
            legacy_alloc[key]=legacy_alloc.get(key,0.0)+max(0.0,_f(amount))

    eth_qty=external_eth_qty+max(0.0,legacy_qty.get("ETH",0.0))+max(0.0,legacy_qty.get("WETH",0.0))
    eth_basis=external_eth_basis+max(0.0,legacy_alloc.get("ETH",0.0))
    eth_unit=(eth_basis/eth_qty) if eth_qty>0 and eth_basis>0 else 0.0

    moves_by_tx=_canonical_audit_moves(store)
    txs=store.list_wallet_audit_transactions(10000)

    # Infer the reviewed GBP basis of USDG/stable inventory from actual ETH->stable
    # swaps. This avoids assuming today's FX rate for historical swaps.
    stable_basis=0.0
    stable_qty=0.0
    for tx in txs:
        if str(tx.get("kind") or "").upper()!="SWAP":
            continue
        key=(str(tx.get("chain") or "").upper(),str(tx.get("tx_hash") or "").lower())
        moves=moves_by_tx.get(key,[])
        out_eth=sum(
            _f(x.get("amount")) for x in moves
            if str(x.get("direction") or "").upper()=="OUT"
            and str(x.get("asset") or "").upper() in {"ETH","WETH"}
        )
        in_stable=sum(
            _f(x.get("amount")) for x in moves
            if str(x.get("direction") or "").upper()=="IN"
            and str(x.get("asset") or "").upper() in {"USDG","USDC","USDT"}
        )
        if out_eth>0 and in_stable>0 and eth_unit>0:
            stable_basis+=out_eth*eth_unit
            stable_qty+=in_stable
    stable_unit=(stable_basis/stable_qty) if stable_qty>0 and stable_basis>0 else 0.0

    basis: dict[str,dict[str,float]]={}
    for symbol,gbp in legacy_alloc.items():
        if symbol in {"ETH","WETH"}:
            continue
        qty=max(0.0,legacy_qty.get(symbol,0.0))
        if qty<=0 or gbp<=0:
            continue
        basis[symbol]={"qty":qty,"basis_gbp":gbp}

    # Direct wallet swaps are the cleanest evidence of asset acquisition. Add the
    # cash-equivalent basis of the spent ETH/WETH/stable to the received asset.
    for tx in sorted(txs,key=lambda x:_f(x.get("occurred_at"))):
        if str(tx.get("kind") or "").upper()!="SWAP":
            continue
        key=(str(tx.get("chain") or "").upper(),str(tx.get("tx_hash") or "").lower())
        moves=moves_by_tx.get(key,[])
        incoming=[
            x for x in moves
            if str(x.get("direction") or "").upper()=="IN"
            and str(x.get("asset") or "").upper() not in {"ETH","WETH","USDG","USDC","USDT","TOKEN","PLAY","CAT","MUSEBOOK"}
        ]
        if len(incoming)!=1:
            continue
        cash_basis=0.0
        for x in moves:
            if str(x.get("direction") or "").upper()!="OUT":
                continue
            symbol=str(x.get("asset") or "").upper()
            amount=max(0.0,_f(x.get("amount")))
            if symbol in {"ETH","WETH"}:
                cash_basis+=amount*eth_unit
            elif symbol in {"USDG","USDC","USDT"}:
                cash_basis+=amount*stable_unit
        if cash_basis<=0:
            continue
        target=str(incoming[0].get("asset") or "").upper()
        qty=max(0.0,_f(incoming[0].get("amount")))
        if qty<=0:
            continue
        row=basis.setdefault(target,{"qty":0.0,"basis_gbp":0.0})
        row["qty"]+=qty
        row["basis_gbp"]+=cash_basis

    basis["ETH"]={
        "qty":max(0.0,eth_qty),
        "basis_gbp":max(0.0,eth_basis),
        "unit_gbp":max(0.0,eth_unit),
    }
    for symbol,row in basis.items():
        qty=max(0.0,_f(row.get("qty")))
        gbp=max(0.0,_f(row.get("basis_gbp")))
        row["unit_gbp"]=(gbp/qty) if qty>0 and gbp>0 else 0.0
    basis["_META"]={"eth_unit_gbp":eth_unit,"stable_unit_gbp":stable_unit}
    return basis


def _clean_ledger_rows(rows: list[dict[str,Any]], acquisition: dict[str,dict[str,float]]) -> list[dict[str,Any]]:
    out=[]
    avg_eth=_f((acquisition.get("_META") or {}).get("eth_unit_gbp"))
    for original in rows:
        row=dict(original)
        if str(row.get("event_type") or "").upper()=="LEGACY_BROUGHT_FORWARD":
            # Operator-approved cleanup: this was real MoonPay funding into the
            # predecessor wallet. Present it like the other cash entries rather
            # than exposing the internal migration/allocation mechanics.
            row["display_label"]="MoonPay funding"
            row["display_note"]=""
            row["display_asset"]="ETH"
            row["display_asset_amount"]=round(_f(row.get("amount_gbp"))/avg_eth,8) if avg_eth>0 else 0.0
            row["display_reference"]="Manual"
            row["display_friction_gbp"]=None
        else:
            row["display_label"]=row.get("label") or ""
            row["display_note"]=row.get("note") or ""
            row["display_asset"]=row.get("asset") or ""
            row["display_asset_amount"]=_f(row.get("asset_amount"))
            row["display_reference"]=row.get("tx_hash") or row.get("source") or ""
            row["display_friction_gbp"]=_f(row.get("estimated_friction_gbp")) if _f(row.get("estimated_friction_gbp"))>0 else None
        out.append(row)
    return out


def _campaign_focus_symbols(campaign: dict[str,Any]) -> set[str]:
    symbol=str(campaign.get("asset_symbol") or "").upper()
    return {"ETH","WETH"} if symbol in {"ETH","WETH"} else ({symbol} if symbol else set())


def _movement_type(kind: str, focus_net: float, lifecycle_type: str = "") -> str:
    lifecycle=str(lifecycle_type or "").upper()
    if lifecycle in {"COLLECT_FEES","FEE_COLLECTION"}:
        return "FEE_COLLECTION"
    if lifecycle in {"OPEN_POSITION","POSITION_OPEN_EVIDENCE"}:
        return "LP_OPEN"
    if lifecycle in {"CLOSE_POSITION","POSITION_CLOSE_EVIDENCE"}:
        return "LP_CLOSE"
    k=str(kind or "").upper()
    if k=="SWAP":
        return "BUY" if focus_net>0 else "SELL" if focus_net<0 else "SWAP"
    if k in {"LP_ACTION","INCREASE_LIQUIDITY","OPEN_POSITION"}:
        return "LP_DEPOSIT" if focus_net<0 else "LP_WITHDRAWAL" if focus_net>0 else "LP_ACTION"
    if k in {"DECREASE_LIQUIDITY","CLOSE_POSITION"}:
        return "LP_WITHDRAWAL"
    if k in {"COLLECT_FEES","FEE_COLLECTION"}:
        return "FEE_COLLECTION"
    if k=="BRIDGE":
        return "TRANSFER_IN" if focus_net>0 else "TRANSFER_OUT" if focus_net<0 else "BRIDGE"
    if k=="NATIVE_TRANSFER":
        return "TRANSFER_IN" if focus_net>0 else "TRANSFER_OUT"
    if k=="APPROVAL":
        return "APPROVAL"
    if focus_net>0:
        return "TRANSFER_IN"
    if focus_net<0:
        return "TRANSFER_OUT"
    return k or "CHAIN_ACTIVITY"


def _campaign_movement_history(store, campaign: dict[str,Any]) -> list[dict[str,Any]]:
    chain=str(campaign.get("chain") or "").upper()
    focus=_campaign_focus_symbols(campaign)
    if not chain or not focus:
        return []

    moves_by_tx=_canonical_audit_moves(store)
    tx_lookup={
        (str(x.get("chain") or "").upper(),str(x.get("tx_hash") or "").lower()):dict(x)
        for x in store.list_wallet_audit_transactions(10000)
    }
    lifecycle_by_tx={}
    for event in list(campaign.get("timeline") or []):
        txh=str(event.get("tx_hash") or "").lower()
        if txh:
            lifecycle_by_tx.setdefault(txh,[]).append(dict(event))

    out=[]
    used=set()
    for key,moves in moves_by_tx.items():
        if key[0]!=chain:
            continue
        relevant=[
            x for x in moves
            if str(x.get("asset") or "").upper() in focus
        ]
        if not relevant:
            continue
        tx=tx_lookup.get(key,{})
        tx_hash=key[1]
        ins=sum(max(0.0,_f(x.get("amount"))) for x in relevant if str(x.get("direction") or "").upper()=="IN")
        outs=sum(max(0.0,_f(x.get("amount"))) for x in relevant if str(x.get("direction") or "").upper()=="OUT")
        net=ins-outs
        lifecycle=list(lifecycle_by_tx.get(tx_hash) or [])
        lifecycle_type=str(lifecycle[0].get("type") or "") if lifecycle else ""
        related=[]
        for x in moves:
            symbol=str(x.get("asset") or "").upper()
            if not symbol or symbol in {"TOKEN","PLAY","CAT","MUSEBOOK"}:
                continue
            related.append({
                "direction":str(x.get("direction") or "").upper(),
                "asset":symbol,
                "amount":round(max(0.0,_f(x.get("amount"))),12),
                "review_status":str(x.get("review_status") or ""),
            })
        when=max(
            [_f(tx.get("occurred_at"))]+[_f(x.get("occurred_at")) for x in relevant]+
            [_f(x.get("time")) for x in lifecycle]
        )
        out.append({
            "time":when,
            "type":_movement_type(str(tx.get("kind") or ""),net,lifecycle_type),
            "chain":chain,
            "tx_hash":str(tx.get("tx_hash") or tx_hash),
            "transaction_kind":str(tx.get("kind") or ""),
            "method":str(tx.get("method") or ""),
            "focus_symbol":str(campaign.get("asset_symbol") or "").upper(),
            "focus_in":round(ins,12),
            "focus_out":round(outs,12),
            "focus_net":round(net,12),
            "transfers":related,
            "gas_native":round(max(0.0,_f(tx.get("gas_native"))),12),
            "native_symbol":str(tx.get("native_symbol") or "ETH"),
            "source":str(tx.get("source") or relevant[0].get("source") or "WALLET_AUDIT"),
            "position_id":str((lifecycle[0] if lifecycle else {}).get("position_id") or ""),
            "position_name":str((lifecycle[0] if lifecycle else {}).get("position_name") or ""),
            "quality":"CHAIN_CONFIRMED",
        })
        used.add(tx_hash)

    # Preserve LP lifecycle events even when no decoded token transfer was
    # available for that transaction. This keeps fee collection/open/close
    # evidence visible without inventing a transfer.
    for event in list(campaign.get("timeline") or []):
        txh=str(event.get("tx_hash") or "").lower()
        if txh and txh in used:
            continue
        etype=str(event.get("type") or "").upper()
        if etype not in {
            "OPEN_POSITION","POSITION_OPEN_EVIDENCE","CLOSE_POSITION",
            "POSITION_CLOSE_EVIDENCE","COLLECT_FEES","FEE_COLLECTION"
        }:
            continue
        out.append({
            "time":_f(event.get("time")),
            "type":_movement_type("",0.0,etype),
            "chain":chain,
            "tx_hash":str(event.get("tx_hash") or ""),
            "transaction_kind":etype,
            "method":"",
            "focus_symbol":str(campaign.get("asset_symbol") or "").upper(),
            "focus_in":0.0,
            "focus_out":0.0,
            "focus_net":0.0,
            "transfers":[],
            "gas_native":0.0,
            "native_symbol":"ETH",
            "source":str(event.get("source") or "POSITION_LEDGER"),
            "position_id":str(event.get("position_id") or ""),
            "position_name":str(event.get("position_name") or ""),
            "amount_usd":_f(event.get("amount_usd")),
            "gas_usd":_f(event.get("gas_usd")),
            "quality":str(event.get("quality") or "EVIDENCE"),
        })
    out.sort(key=lambda x:_f(x.get("time")),reverse=True)
    return out


def enrich_campaigns_with_capital(
    settings,
    store,
    campaigns: list[dict[str,Any]] | None = None,
) -> list[dict[str,Any]]:
    rows=store.list_capital_ledger(1000)
    acquisition=_transaction_acquisition_basis(settings,store,rows) if rows else {}
    money=money_context(settings,store)
    rate=max(1e-12,_f(money.get("usd_to_display_rate"),1.0))
    source=list(campaigns) if campaigns is not None else build_campaigns(store)
    out=[]

    for original in source:
        c=dict(original)
        symbol=str(c.get("asset_symbol") or "").upper()
        wallet=dict(c.get("wallet_inventory") or {})
        wallet_balance=max(0.0,_f(wallet.get("balance")))
        marked_usd=max(0.0,_f(c.get("marked_exposure_usd")))

        acq=acquisition.get("ETH" if symbol in {"ETH","WETH"} else symbol,{}) or {}
        unit_basis_gbp=max(0.0,_f(acq.get("unit_gbp")))
        total_asset_basis_gbp=max(0.0,_f(acq.get("basis_gbp")))
        wallet_basis_gbp=(
            min(total_asset_basis_gbp,wallet_balance*unit_basis_gbp)
            if wallet_balance>0 and unit_basis_gbp>0 else 0.0
        )
        open_position_basis_gbp=sum(
            max(0.0,_f(p.get("opening_capital_usd")))*rate
            for p in list(c.get("positions") or [])
            if str(p.get("status") or "").upper()=="OPEN"
        )
        capital_gbp=max(0.0,open_position_basis_gbp+wallet_basis_gbp)
        capital_usd=capital_gbp/rate
        exposure_pnl_usd=marked_usd-capital_usd
        exposure_return_pct=(exposure_pnl_usd/capital_usd*100.0) if capital_usd>0 else None
        wallet_basis_usd=wallet_basis_gbp/rate

        if wallet_balance>0 and wallet_basis_gbp<=0:
            quality="PARTIAL_WALLET_BASIS"
        elif any(
            str(p.get("pnl_quality") or "").upper()=="INCOMPLETE"
            for p in list(c.get("positions") or [])
        ):
            quality="PARTIAL_POSITION_EVIDENCE"
        else:
            quality="CAMPAIGN_CAPITAL_TRACED"

        wallet.update({
            "basis_usd":round(wallet_basis_usd,6) if wallet_balance>0 else 0.0,
            "attribution":"TRANSACTION_TRACED_ACQUISITION_BASIS" if wallet_balance>0 and wallet_basis_gbp>0 else wallet.get("attribution"),
            "quality":"TRANSACTION_TRACED_COST_BASIS" if wallet_balance>0 and wallet_basis_gbp>0 else wallet.get("quality"),
        })
        c["wallet_inventory"]=wallet
        c["capital_invested_usd"]=round(capital_usd,6)
        c["capital_invested_gbp"]=round(capital_gbp,2)
        c["wallet_inventory_basis_usd"]=round(wallet_basis_usd,6)
        c["open_position_basis_usd"]=round(open_position_basis_gbp/rate,6)
        c["exposure_pnl_usd"]=round(exposure_pnl_usd,6)
        c["exposure_return_pct"]=round(exposure_return_pct,2) if exposure_return_pct is not None else None
        c["profit_cushion_usd"]=round(max(0.0,exposure_pnl_usd),6)
        c["capital_shortfall_usd"]=round(max(0.0,-exposure_pnl_usd),6)
        c["capital_state"]="ABOVE_BASIS" if exposure_pnl_usd>0.01 else "BELOW_BASIS" if exposure_pnl_usd<-0.01 else "AT_BASIS"
        c["accounting_quality"]=quality
        c["accounting_complete"]=quality=="CAMPAIGN_CAPITAL_TRACED"
        c["movement_history"]=_campaign_movement_history(store,c)
        c["capital_accounting"]={
            "capital_invested_usd":c["capital_invested_usd"],
            "marked_exposure_usd":round(marked_usd,6),
            "exposure_pnl_usd":c["exposure_pnl_usd"],
            "exposure_return_pct":c["exposure_return_pct"],
            "profit_cushion_usd":c["profit_cushion_usd"],
            "capital_shortfall_usd":c["capital_shortfall_usd"],
            "state":c["capital_state"],
            "basis_method":"OPEN_POSITION_BASIS_PLUS_TRANSACTION_TRACED_WALLET_BASIS",
            "movement_count":len(c["movement_history"]),
        }
        provenance=dict(c.get("provenance") or {})
        provenance.update({
            "wallet_inventory_quality":wallet.get("quality") or provenance.get("wallet_inventory_quality"),
            "wallet_inventory_included_in_pnl":bool(wallet_balance<=0 or wallet_basis_gbp>0),
            "capital_basis_method":"OPEN_POSITION_BASIS_PLUS_TRANSACTION_TRACED_WALLET_BASIS",
            "wallet_audit_movements":len(c["movement_history"]),
            "note":"Campaign accounting now combines child LP evidence with transaction-traced wallet acquisition basis. Raw wallet-audit movements remain independently auditable.",
        })
        c["provenance"]=provenance
        out.append(c)

    out.sort(key=lambda x:(0 if x.get("status")=="ACTIVE" else 1,-_f(x.get("marked_exposure_usd")),str(x.get("label") or "")))
    return out


def campaign_with_capital(settings, store, campaign_id: str) -> dict[str,Any] | None:
    return next(
        (x for x in enrich_campaigns_with_capital(settings,store) if str(x.get("id"))==str(campaign_id)),
        None,
    )


def clean_capital_ledger(settings, store) -> dict[str,Any]:
    raw_rows=store.list_capital_ledger(1000)
    invested=sum(max(0.0,_f(x.get("amount_gbp"))) for x in raw_rows if str(x.get("event_type") or "").upper() in FUNDING_TYPES)
    withdrawn=sum(max(0.0,_f(x.get("amount_gbp"))) for x in raw_rows if str(x.get("event_type") or "").upper() in WITHDRAWAL_TYPES)
    friction=sum(max(0.0,_f(x.get("estimated_friction_gbp"))) for x in raw_rows)
    current=_capital_current_gbp(settings,store)
    pnl=current+withdrawn-invested
    ret=(pnl/invested*100.0) if invested>0 else None

    acquisition=_transaction_acquisition_basis(settings,store,raw_rows) if raw_rows else {}
    rows=_clean_ledger_rows(raw_rows,acquisition) if raw_rows else []

    money=money_context(settings,store)
    usd_to_gbp=_f(money.get("usd_to_display_rate"),1.0)
    campaigns=[]
    for c in enrich_campaigns_with_capital(settings,store):
        symbol=str(c.get("asset_symbol") or "").upper()
        campaigns.append({
            "campaign_id":c.get("id"),
            "symbol":symbol,
            "label":c.get("label") or symbol,
            "status":c.get("status"),
            "capital_invested_gbp":round(_f(c.get("capital_invested_usd"))*usd_to_gbp,2),
            "marked_exposure_gbp":round(_f(c.get("marked_exposure_usd"))*usd_to_gbp,2),
            "exposure_pnl_gbp":round(_f(c.get("exposure_pnl_usd"))*usd_to_gbp,2),
            "exposure_return_pct":c.get("exposure_return_pct"),
            "lp_strategy_pnl_gbp":round(_f(c.get("known_campaign_pnl_usd"))*usd_to_gbp,2),
            "lifetime_fees_gbp":round(_f(c.get("lifetime_fees_usd"))*usd_to_gbp,2),
            "transaction_costs_gbp":round(_f(c.get("transaction_costs_usd"))*usd_to_gbp,2),
            "wallet_inventory_basis_gbp":round(_f(c.get("wallet_inventory_basis_usd"))*usd_to_gbp,2),
            "wallet_inventory_value_gbp":round(_f((c.get("wallet_inventory") or {}).get("value_usd"))*usd_to_gbp,2),
            "open_position_basis_gbp":round(_f(c.get("open_position_basis_usd"))*usd_to_gbp,2),
            "basis_method":str((c.get("capital_accounting") or {}).get("basis_method") or ""),
            "movement_count":len(c.get("movement_history") or []),
            "open_positions":int(c.get("open_positions") or 0),
            "closed_positions":int(c.get("closed_positions") or 0),
        })

    campaigns.sort(key=lambda x:(0 if x["status"]=="ACTIVE" else 1,-x["marked_exposure_gbp"],x["label"]))
    baseline=store.get_setting("capital_ledger:baseline_meta",None)
    return {
        "currency":"GBP",
        "baseline_installed":bool(raw_rows),
        "baseline_meta":baseline,
        "metrics":{
            "cash_invested_gbp":round(invested,2),
            "cash_withdrawn_gbp":round(withdrawn,2),
            "current_portfolio_gbp":round(current,2),
            "lifetime_pnl_gbp":round(pnl,2),
            "lifetime_return_pct":round(ret,2) if ret is not None else None,
            "estimated_onramp_friction_gbp":round(friction,2),
            "net_cash_invested_gbp":round(invested-withdrawn,2),
        },
        "acquisition_basis":{
            k:{kk:round(_f(vv),8) for kk,vv in v.items()}
            for k,v in acquisition.items()
            if k!="_META"
        },
        "entries":rows,
        "campaigns":campaigns,
        "note":"Investor P/L uses reviewed external cash. Campaign capital combines verified open-position basis with wallet-asset acquisition basis traced from reviewed funding and on-chain swaps; it is not cumulative LP turnover.",
    }

