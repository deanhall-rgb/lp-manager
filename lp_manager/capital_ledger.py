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
    for c in build_campaigns(store):
        symbol=str(c.get("asset_symbol") or "").upper()
        marked=round(_f(c.get("marked_exposure_usd"))*usd_to_gbp,2)
        lp_pnl=round(_f(c.get("known_campaign_pnl_usd"))*usd_to_gbp,2)
        wallet=dict(c.get("wallet_inventory") or {})
        wallet_value=max(0.0,_f(wallet.get("value_usd"))*usd_to_gbp)
        wallet_balance=max(0.0,_f(wallet.get("balance")))

        acq=acquisition.get("ETH" if symbol in {"ETH","WETH"} else symbol,{}) or {}
        unit_basis=max(0.0,_f(acq.get("unit_gbp")))
        total_asset_basis=max(0.0,_f(acq.get("basis_gbp")))
        wallet_basis=min(total_asset_basis,wallet_balance*unit_basis) if wallet_balance>0 and unit_basis>0 else 0.0

        open_position_basis=sum(
            max(0.0,_f(p.get("opening_capital_usd")))*usd_to_gbp
            for p in list(c.get("positions") or [])
            if str(p.get("status") or "").upper()=="OPEN"
        )
        capital_basis=max(0.0,open_position_basis+wallet_basis)
        exposure_pnl=marked-capital_basis

        campaigns.append({
            "campaign_id":c.get("id"),
            "symbol":symbol,
            "label":c.get("label") or symbol,
            "status":c.get("status"),
            "capital_invested_gbp":round(capital_basis,2),
            "marked_exposure_gbp":marked,
            "exposure_pnl_gbp":round(exposure_pnl,2),
            "lp_strategy_pnl_gbp":lp_pnl,
            "lifetime_fees_gbp":round(_f(c.get("lifetime_fees_usd"))*usd_to_gbp,2),
            "transaction_costs_gbp":round(_f(c.get("transaction_costs_usd"))*usd_to_gbp,2),
            "wallet_inventory_basis_gbp":round(wallet_basis,2),
            "wallet_inventory_value_gbp":round(wallet_value,2),
            "open_position_basis_gbp":round(open_position_basis,2),
            "basis_method":"OPEN_POSITION_BASIS_PLUS_TRANSACTION_TRACED_WALLET_BASIS",
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

