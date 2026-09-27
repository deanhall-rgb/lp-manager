from __future__ import annotations

import math
from typing import Any

from .portfolio_accounting import position_accounting


BASE_QUOTE_ASSETS={
    "ETH","WETH","USDC","USDT","USDG","DAI","USDS","USDE","PYUSD","EURC"
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _pair_symbols(position: dict[str,Any]) -> list[str]:
    pair=str(position.get("pair") or "").replace(" ","").upper()
    return [x for x in pair.split("/") if x][:2]


def _snapshot_tokens(position: dict[str,Any], snapshot: dict[str,Any]) -> list[dict[str,Any]]:
    out=[]
    for key in ("token0","token1"):
        row=dict(snapshot.get(key) or {})
        if row.get("symbol"):
            out.append({
                "symbol":str(row.get("symbol") or "").upper(),
                "address":str(row.get("address") or "").lower(),
                "amount":max(0.0,_f(row.get("amount"))),
                "price_usd":max(0.0,_f(row.get("price_usd"))),
                "value_usd":max(0.0,_f(row.get("amount")))*max(0.0,_f(row.get("price_usd"))),
            })
    if len(out)>=2:
        return out[:2]

    historical=dict(snapshot.get("historical_evidence") or {})
    for idx in (0,1):
        symbol=str(
            historical.get(f"token{idx}_symbol")
            or snapshot.get(f"token{idx}_symbol")
            or ""
        ).upper()
        address=str(
            historical.get(f"token{idx}_address")
            or snapshot.get(f"token{idx}_address")
            or ""
        ).lower()
        if symbol and not any(x["symbol"]==symbol for x in out):
            out.append({"symbol":symbol,"address":address,"amount":0.0,"price_usd":0.0,"value_usd":0.0})

    if len(out)<2:
        for symbol in _pair_symbols(position):
            if not any(x["symbol"]==symbol for x in out):
                out.append({"symbol":symbol,"address":"","amount":0.0,"price_usd":0.0,"value_usd":0.0})
    return out[:2]


def campaign_identity(position: dict[str,Any], snapshot: dict[str,Any] | None = None) -> dict[str,Any]:
    """Choose the durable asset-level campaign identity for one LP leg.

    One non-base asset against ETH/WETH/stables becomes the campaign asset.
    WETH/stable pools form a WETH Income campaign. Two non-base assets are kept as
    a pair campaign until the operator explicitly chooses a thesis asset in a later
    campaign-management iteration.
    """
    snapshot=snapshot or {}
    chain=str(position.get("chain") or "UNKNOWN").upper()
    tokens=_snapshot_tokens(position,snapshot)
    non_base=[t for t in tokens if t.get("symbol") and t["symbol"] not in BASE_QUOTE_ASSETS]

    if len(non_base)==1:
        focus=non_base[0]
        symbol=focus["symbol"]
        return {
            "id":f"campaign:{chain}:{symbol}",
            "chain":chain,
            "asset_symbol":symbol,
            "asset_address":focus.get("address") or "",
            "label":symbol,
            "identity_quality":"ASSET_ADDRESS_AND_SYMBOL" if focus.get("address") else "SYMBOL_DERIVED",
            "kind":"ASSET",
        }

    symbols=[t["symbol"] for t in tokens if t.get("symbol")]
    if not non_base and any(x in symbols for x in ("ETH","WETH")):
        return {
            "id":f"campaign:{chain}:ETH",
            "chain":chain,
            "asset_symbol":"ETH",
            "asset_address":"",
            "label":"ETH",
            "identity_quality":"ETH_WETH_EQUIVALENCE",
            "kind":"INCOME",
        }

    pair="-".join(symbols or _pair_symbols(position) or ["UNKNOWN"])
    return {
        "id":f"campaign:{chain}:PAIR:{pair}",
        "chain":chain,
        "asset_symbol":pair,
        "asset_address":"",
        "label":pair.replace("-","/"),
        "identity_quality":"PAIR_DERIVED",
        "kind":"PAIR",
    }


def _position_metrics(store, position: dict[str,Any]) -> dict[str,Any]:
    pid=str(position.get("id") or "")
    snapshot=store.get_position_snapshot(pid) or {}
    tracker=store.get_setting(f"fees:tracker:{pid}",{}) or {}
    acct=position_accounting(position,snapshot,tracker)
    closed_final=dict(snapshot.get("closed_final") or {})
    status=str(position.get("status") or "").upper()

    if status=="CLOSED":
        opening=max(0.0,_f(closed_final.get("opening_capital_usd") or acct.get("cost_basis_usd")))
        fees=max(0.0,_f(closed_final.get("total_fees_usd") or position.get("realised_fees")))
        costs=max(0.0,_f(closed_final.get("transaction_costs_usd") or closed_final.get("gas_usd") or position.get("gas_costs")))
        if closed_final.get("complete"):
            pnl=_f(closed_final.get("realised_pnl_usd"))
            pnl_quality=str(closed_final.get("quality") or position.get("pnl_quality") or "FINAL")
        elif str(position.get("pnl_quality") or "").upper() not in {"","UNKNOWN","FIRST_OBSERVED"}:
            pnl=_f(position.get("reported_net_pnl"))
            pnl_quality=str(position.get("pnl_quality") or "REPORTED")
        else:
            pnl=None
            pnl_quality="INCOMPLETE"
        current_lp=0.0
    else:
        opening=max(0.0,_f(acct.get("cost_basis_usd")))
        fees=max(0.0,_f(acct.get("fees_earned_usd")))
        costs=max(0.0,_f(acct.get("gas_costs_usd")))
        pnl=acct.get("net_pnl_after_costs_usd")
        pnl=_f(pnl) if pnl is not None else None
        pnl_quality=str(acct.get("quality") or "UNKNOWN")
        current_lp=max(0.0,_f(acct.get("current_principal_usd")))

    tokens=_snapshot_tokens(position,snapshot)
    return {
        "id":pid,
        "display_name":position.get("display_name") or position.get("pair") or pid,
        "pair":position.get("pair"),
        "chain":position.get("chain"),
        "status":status,
        "opened_at":_f(position.get("opened_at")),
        "closed_at":_f(closed_final.get("closed_at") or position.get("closed_at")),
        "opening_capital_usd":round(opening,6),
        "current_lp_value_usd":round(current_lp,6),
        "fees_usd":round(fees,6),
        "transaction_costs_usd":round(costs,6),
        "net_pnl_usd":round(pnl,6) if pnl is not None else None,
        "pnl_quality":pnl_quality,
        "basis_ready":bool(acct.get("basis_ready")),
        "source":position.get("source"),
        "lifecycle_stage":position.get("lifecycle_stage"),
        "token_id":position.get("token_id"),
        "pool_address":position.get("pool_address"),
        "tokens":tokens,
        "snapshot":snapshot,
    }


def _wallet_inventory(wallet: dict[str,Any], identity: dict[str,Any]) -> dict[str,Any]:
    symbol=str(identity.get("asset_symbol") or "").upper()
    address=str(identity.get("asset_address") or "").lower()
    chain=str(identity.get("chain") or "").upper()
    if identity.get("kind")=="PAIR":
        return {
            "balance":0.0,"value_usd":0.0,"price_usd":0.0,
            "basis_usd":None,"attribution":"NOT_APPLICABLE_PAIR_CAMPAIGN",
            "quality":"NOT_APPLICABLE","holdings":[],
        }

    rows=[]
    eth_equivalent=identity.get("kind")=="INCOME" and symbol=="ETH"
    for row in list(wallet.get("holdings") or [])+list(wallet.get("hidden_holdings") or []):
        if str(row.get("chain") or "").upper()!=chain:
            continue
        row_symbol=str(row.get("symbol") or "").upper()
        row_address=str(row.get("address") or "").lower()
        if eth_equivalent:
            if row_symbol not in {"ETH","WETH"}:
                continue
        elif address:
            if row_address!=address:
                continue
        elif row_symbol!=symbol:
            continue
        rows.append(dict(row))

    # ETH and WETH are one economic asset for campaign reporting. Preserve the raw
    # holdings below for provenance, but aggregate balances/value at 1:1 ETH units.
    balance=sum(max(0.0,_f(x.get("balance"))) for x in rows)
    value=sum(max(0.0,_f(x.get("value_usd"))) for x in rows)
    price=(value/balance) if balance>0 and value>0 else max([_f(x.get("price_usd")) for x in rows] or [0.0])
    return {
        "balance":round(balance,12),
        "value_usd":round(value,6),
        "price_usd":round(price,10),
        "basis_usd":None,
        "attribution":"CURRENT_WALLET_BALANCE_MATCHED_TO_CAMPAIGN_ASSET",
        "quality":"UNATTRIBUTED_COST_BASIS" if balance>0 else "NO_WALLET_INVENTORY",
        "holdings":[{
            "chain":x.get("chain"),"symbol":x.get("symbol"),"address":x.get("address"),
            "balance":x.get("balance"),"value_usd":x.get("value_usd"),
            "data_quality":x.get("data_quality"),"discovery_source":x.get("discovery_source"),
        } for x in rows],
    }


def sync_campaign_registry(store) -> dict[str,Any]:
    linked=0
    campaign_ids=set()
    for position in store.list_positions():
        if str(position.get("monitoring_class") or "").upper()=="ARCHIVED_SUPERSEDED":
            continue
        if str(position.get("source") or "")=="legacy_campaign_ledger":
            continue
        pid=str(position.get("id") or "")
        snapshot=store.get_position_snapshot(pid) or {}
        identity=campaign_identity(position,snapshot)
        store.upsert_campaign(
            campaign_id=identity["id"],
            chain=identity["chain"],
            asset_symbol=identity["asset_symbol"],
            asset_address=identity["asset_address"],
            label=identity["label"],
            status="ACTIVE" if str(position.get("status") or "").upper()=="OPEN" else "HISTORY",
            identity_quality=identity["identity_quality"],
        )
        store.set_position_campaign(pid,identity["id"],identity["label"])
        campaign_ids.add(identity["id"])
        linked+=1
    return {"positions_linked":linked,"campaigns":len(campaign_ids)}


def build_campaigns(store) -> list[dict[str,Any]]:
    sync_campaign_registry(store)
    wallet=store.get_wallet_snapshot() or {}
    positions=[
        p for p in store.list_positions()
        if str(p.get("monitoring_class") or "").upper()!="ARCHIVED_SUPERSEDED"
        and str(p.get("source") or "")!="legacy_campaign_ledger"
    ]
    metrics=[_position_metrics(store,p) for p in positions]
    by_id={str(p.get("id") or ""):p for p in positions}
    events=store.list_financial_events(5000)
    out=[]

    for campaign in store.list_campaigns():
        cid=str(campaign.get("id") or "")
        legs=[m for m in metrics if str(by_id.get(m["id"],{}).get("campaign_id") or "")==cid]
        if not legs:
            continue
        identity={
            "id":cid,
            "chain":campaign.get("chain"),
            "asset_symbol":campaign.get("asset_symbol"),
            "asset_address":campaign.get("asset_address"),
            "label":campaign.get("label"),
            "identity_quality":campaign.get("identity_quality"),
            "kind":"PAIR" if ":PAIR:" in cid else ("INCOME" if cid.endswith(":ETH") else "ASSET"),
        }
        wallet_inv=_wallet_inventory(wallet,identity)
        open_legs=[x for x in legs if x["status"]=="OPEN"]
        closed_legs=[x for x in legs if x["status"]=="CLOSED"]
        realised=sum(_f(x.get("net_pnl_usd")) for x in closed_legs if x.get("net_pnl_usd") is not None)
        open_pnl=sum(_f(x.get("net_pnl_usd")) for x in open_legs if x.get("net_pnl_usd") is not None)
        known_pnl=realised+open_pnl
        fees=sum(max(0.0,_f(x.get("fees_usd"))) for x in legs)
        costs=sum(max(0.0,_f(x.get("transaction_costs_usd"))) for x in legs)
        opening=sum(max(0.0,_f(x.get("opening_capital_usd"))) for x in legs)
        open_lp=sum(max(0.0,_f(x.get("current_lp_value_usd"))) for x in open_legs)
        unresolved=sum(1 for x in legs if x.get("net_pnl_usd") is None)

        pids={x["id"] for x in legs}
        campaign_events=[
            e for e in events
            if str(e.get("campaign_id") or "")==cid
            or str(e.get("position_id") or "") in pids
        ]
        timeline=[]
        event_position={str(x["id"]):x for x in legs}
        event_types_by_position={}
        for event in campaign_events:
            pid=str(event.get("position_id") or "")
            event_types_by_position.setdefault(pid,set()).add(str(event.get("event_type") or "").upper())
            timeline.append({
                "time":_f(event.get("occurred_at") or event.get("created_at")),
                "type":str(event.get("event_type") or "FINANCIAL_EVENT").upper(),
                "position_id":pid,
                "position_name":event_position.get(pid,{}).get("display_name"),
                "tx_hash":event.get("tx_hash"),
                "amount_usd":_f(event.get("amount_usd")),
                "gas_usd":_f(event.get("gas_usd")),
                "source":"FINANCIAL_EVENT",
                "quality":"CONFIRMED" if str(event.get("status") or "").upper()=="CONFIRMED" else str(event.get("status") or ""),
            })
        for leg in legs:
            types=event_types_by_position.get(leg["id"],set())
            if "OPEN_POSITION" not in types and leg.get("opened_at"):
                timeline.append({
                    "time":leg["opened_at"],"type":"POSITION_OPEN_EVIDENCE",
                    "position_id":leg["id"],"position_name":leg["display_name"],
                    "tx_hash":(leg.get("snapshot") or {}).get("entry_evidence",{}).get("transaction_hash"),
                    "amount_usd":leg.get("opening_capital_usd"),"gas_usd":0.0,
                    "source":"POSITION_RECORD","quality":leg.get("pnl_quality"),
                })
            if leg["status"]=="CLOSED" and "CLOSE_POSITION" not in types and leg.get("closed_at"):
                final=dict((leg.get("snapshot") or {}).get("closed_final") or {})
                timeline.append({
                    "time":leg["closed_at"],"type":"POSITION_CLOSE_EVIDENCE",
                    "position_id":leg["id"],"position_name":leg["display_name"],
                    "tx_hash":final.get("close_transaction_hash"),
                    "amount_usd":final.get("close_proceeds_usd"),"gas_usd":final.get("gas_usd",0),
                    "source":"POSITION_RECORD","quality":final.get("quality") or leg.get("pnl_quality"),
                })
        timeline.sort(key=lambda x:x.get("time") or 0)

        wallet_basis_unknown=wallet_inv["balance"]>0 and wallet_inv.get("basis_usd") is None
        accounting_quality=(
            "PARTIAL_WALLET_BASIS" if wallet_basis_unknown
            else "PARTIAL_POSITION_EVIDENCE" if unresolved
            else "POSITION_LEDGER_COMPLETE"
        )
        status="ACTIVE" if open_legs else ("HOLDING" if wallet_inv["balance"]>0 else "CLOSED")

        out.append({
            **identity,
            "status":status,
            "position_count":len(legs),
            "open_positions":len(open_legs),
            "closed_positions":len(closed_legs),
            "opening_capital_across_legs_usd":round(opening,6),
            "current_lp_value_usd":round(open_lp,6),
            "lifetime_fees_usd":round(fees,6),
            "transaction_costs_usd":round(costs,6),
            "realised_position_pnl_usd":round(realised,6),
            "open_position_pnl_usd":round(open_pnl,6),
            "known_campaign_pnl_usd":round(known_pnl,6),
            "wallet_inventory":wallet_inv,
            "marked_exposure_usd":round(open_lp+wallet_inv["value_usd"],6),
            "accounting_quality":accounting_quality,
            "accounting_complete":accounting_quality=="POSITION_LEDGER_COMPLETE",
            "positions":[{
                k:v for k,v in leg.items()
                if k not in {"snapshot","tokens"}
            } | {"focus_inventory":next((
                {"symbol":t["symbol"],"amount":t["amount"],"value_usd":t["value_usd"]}
                for t in leg["tokens"]
                if t["symbol"]==str(identity.get("asset_symbol") or "").upper()
            ),None)}
                for leg in sorted(legs,key=lambda x:x.get("opened_at") or 0)
            ],
            "timeline":timeline,
            "provenance":{
                "campaign_identity":identity["identity_quality"],
                "linked_positions":len(legs),
                "verified_position_bases":sum(1 for x in legs if x.get("basis_ready")),
                "financial_events":len(campaign_events),
                "wallet_inventory_quality":wallet_inv["quality"],
                "wallet_inventory_included_in_pnl":False,
                "note":"Position results are aggregated without rewriting child LP outcomes. Current campaign-asset wallet inventory is shown separately and excluded from campaign P/L until its acquisition basis can be proven.",
            },
        })

    out.sort(key=lambda x:(0 if x["status"]=="ACTIVE" else 1, -x["marked_exposure_usd"], x["label"]))
    return out


def campaign_by_id(store, campaign_id: str) -> dict[str,Any] | None:
    return next((x for x in build_campaigns(store) if str(x.get("id"))==str(campaign_id)),None)
