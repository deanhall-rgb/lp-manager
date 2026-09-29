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


def clean_capital_ledger(settings, store) -> dict[str,Any]:
    rows=store.list_capital_ledger(1000)
    invested=sum(max(0.0,_f(x.get("amount_gbp"))) for x in rows if str(x.get("event_type") or "").upper() in FUNDING_TYPES)
    withdrawn=sum(max(0.0,_f(x.get("amount_gbp"))) for x in rows if str(x.get("event_type") or "").upper() in WITHDRAWAL_TYPES)
    friction=sum(max(0.0,_f(x.get("estimated_friction_gbp"))) for x in rows)
    current=_capital_current_gbp(settings,store)
    pnl=current+withdrawn-invested
    ret=(pnl/invested*100.0) if invested>0 else None

    allocation_basis={}
    for row in rows:
        meta=dict(row.get("metadata") or {})
        for symbol,amount in dict(meta.get("allocations_gbp") or {}).items():
            key=str(symbol or "").upper()
            allocation_basis[key]=allocation_basis.get(key,0.0)+max(0.0,_f(amount))

    money=money_context(settings,store)
    usd_to_gbp=_f(money.get("usd_to_display_rate"),1.0)
    campaigns=[]
    for c in build_campaigns(store):
        symbol=str(c.get("asset_symbol") or "").upper()
        campaigns.append({
            "campaign_id":c.get("id"),
            "symbol":symbol,
            "label":c.get("label") or symbol,
            "status":c.get("status"),
            "marked_exposure_gbp":round(_f(c.get("marked_exposure_usd"))*usd_to_gbp,2),
            "lp_strategy_pnl_gbp":round(_f(c.get("known_campaign_pnl_usd"))*usd_to_gbp,2),
            "lifetime_fees_gbp":round(_f(c.get("lifetime_fees_usd"))*usd_to_gbp,2),
            "transaction_costs_gbp":round(_f(c.get("transaction_costs_usd"))*usd_to_gbp,2),
            "reviewed_legacy_basis_gbp":round(allocation_basis.get(symbol,0.0),2),
            "open_positions":int(c.get("open_positions") or 0),
            "closed_positions":int(c.get("closed_positions") or 0),
        })

    campaigns.sort(key=lambda x:(0 if x["status"]=="ACTIVE" else 1,-x["marked_exposure_gbp"],x["label"]))
    baseline=store.get_setting("capital_ledger:baseline_meta",None)
    return {
        "currency":"GBP",
        "baseline_installed":bool(rows),
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
        "legacy_campaign_basis_gbp":{k:round(v,2) for k,v in sorted(allocation_basis.items())},
        "entries":rows,
        "campaigns":campaigns,
        "note":"The clean ledger contains reviewed investor cash flows only. Internal bridges, swaps, LP mechanics, approvals and spam remain in advanced audit evidence and do not alter external capital invested.",
    }
