from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime
from typing import Any

import requests

from .chain_registry import CHAINS, ChainConfig
from .fx import money_context


REVIEWABLE = {"UNRESOLVED"}
RESOLVED_FUNDING = "CONFIRMED_FUNDING"
RESOLVED_WITHDRAWAL = "CONFIRMED_WITHDRAWAL"
INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
INTERNAL_PROTOCOL = "INTERNAL_PROTOCOL"
INTERNAL_CONVERSION = "INTERNAL_CONVERSION"
IGNORED = "IGNORE"

_SUSPICIOUS = re.compile(r"(?i)(https?://|www\.|\bclaim\b|airdrop|reward|voucher|visit\s)")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _int_any(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str) and value.lower().startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def _norm_address(value: Any) -> str:
    return str(value or "").strip().lower()


def _iso_ts(value: Any) -> float:
    raw=str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z","+00:00")).timestamp()
    except Exception:
        return 0.0


def _event_id(chain: str, tx_hash: str, unique: str, direction: str, asset: str, amount: float) -> str:
    seed="|".join([str(chain).upper(),str(tx_hash).lower(),str(unique),str(direction),str(asset),f"{amount:.18g}"])
    return "wa:"+hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _tx_id(chain: str, tx_hash: str) -> str:
    seed=f"{str(chain).upper()}|{str(tx_hash).lower()}"
    return "wat:"+hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _alchemy_url(cfg: ChainConfig) -> str:
    key=os.getenv("ALCHEMY_API_KEY","").strip()
    return f"https://{cfg.alchemy_slug}.g.alchemy.com/v2/{key}" if key and cfg.alchemy_slug else ""


def _rpc(url: str, method: str, params: list[Any]) -> Any:
    if not url:
        raise RuntimeError("RPC unavailable")
    r=requests.post(url,json={"jsonrpc":"2.0","id":1,"method":method,"params":params},timeout=30)
    r.raise_for_status()
    payload=r.json()
    if payload.get("error"):
        raise RuntimeError(str((payload.get("error") or {}).get("message") or f"{method} failed"))
    return payload.get("result")


def _alchemy_direction(cfg: ChainConfig, wallet: str, direction: str, *, max_pages: int = 20) -> list[dict[str,Any]]:
    url=_alchemy_url(cfg)
    if not url:
        return []
    rows=[]
    page_key=None
    for _ in range(max_pages):
        params={
            "fromBlock":"0x0",
            "toBlock":"latest",
            "category":["external","internal","erc20"],
            "withMetadata":True,
            "excludeZeroValue":True,
            "maxCount":"0x3e8",
            "order":"asc",
        }
        params["toAddress" if direction=="IN" else "fromAddress"]=wallet
        if page_key:
            params["pageKey"]=page_key
        r=requests.post(url,json={"jsonrpc":"2.0","id":1,"method":"alchemy_getAssetTransfers","params":[params]},timeout=35)
        r.raise_for_status()
        payload=r.json()
        if payload.get("error"):
            raise RuntimeError(str((payload.get("error") or {}).get("message") or "Alchemy asset-transfer error"))
        result=payload.get("result") or {}
        rows.extend(result.get("transfers") or [])
        page_key=result.get("pageKey")
        if not page_key:
            break
    return rows


def _alchemy_transfers(cfg: ChainConfig, wallet: str) -> list[dict[str,Any]]:
    raw=_alchemy_direction(cfg,wallet,"IN")+_alchemy_direction(cfg,wallet,"OUT")
    out=[]
    wallet_l=_norm_address(wallet)
    seen=set()
    for idx,row in enumerate(raw):
        tx=str(row.get("hash") or "")
        frm=_norm_address(row.get("from"))
        to=_norm_address(row.get("to"))
        direction="SELF" if frm==wallet_l and to==wallet_l else "IN" if to==wallet_l else "OUT" if frm==wallet_l else ""
        if not direction:
            continue
        asset=str(row.get("asset") or "TOKEN").strip() or "TOKEN"
        if _SUSPICIOUS.search(asset):
            continue
        amount=max(0.0,_f(row.get("value")))
        if amount<=0:
            continue
        raw_contract=row.get("rawContract") or {}
        token_address=str(raw_contract.get("address") or "")
        unique=str(row.get("uniqueId") or f"{idx}:{row.get('category') or ''}:{token_address}")
        metadata=row.get("metadata") or {}
        occurred=_iso_ts(metadata.get("blockTimestamp"))
        key=(tx.lower(),unique,direction)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id":_event_id(cfg.key,tx,unique,direction,asset,amount),
            "chain":cfg.key,
            "tx_hash":tx,
            "transfer_key":unique,
            "occurred_at":occurred,
            "direction":direction,
            "category":str(row.get("category") or ""),
            "asset":asset,
            "token_address":token_address,
            "amount":amount,
            "from_address":str(row.get("from") or ""),
            "to_address":str(row.get("to") or ""),
            "source":"ALCHEMY_ASSET_TRANSFERS",
            "payload":{"block_num":row.get("blockNum"),"raw_contract":raw_contract},
        })
    return out


def _blockscout_base(cfg: ChainConfig) -> tuple[str, dict[str,str]]:
    api_key=os.getenv("BLOCKSCOUT_API_KEY","").strip()
    if api_key:
        return f"https://api.blockscout.com/{cfg.chain_id}/api/v2",{"authorization":f"Bearer {api_key}"}
    return str(cfg.explorer_api_base or "").rstrip("/"),{}


def _blockscout_pages(cfg: ChainConfig, path: str, *, max_pages: int = 40) -> list[dict[str,Any]]:
    base,auth=_blockscout_base(cfg)
    if not base:
        return []
    rows=[]
    params={}
    headers={"Accept":"application/json","User-Agent":"LP-Manager/0.9.5",**auth}
    for _ in range(max_pages):
        url=base.rstrip("/")+"/"+path.lstrip("/")
        r=requests.get(url,params=params,timeout=35,headers=headers)
        r.raise_for_status()
        payload=r.json()
        if isinstance(payload,list):
            rows.extend(payload)
            break
        items=payload.get("items") or []
        rows.extend(items)
        nxt=payload.get("next_page_params") or {}
        if not nxt:
            break
        params=nxt
    return rows


def _blockscout_available(cfg: ChainConfig) -> bool:
    return bool(os.getenv("BLOCKSCOUT_API_KEY","").strip() or cfg.explorer_api_base)


def _addr_value(value: Any) -> tuple[str,str]:
    if isinstance(value,dict):
        return str(value.get("hash") or ""),str(value.get("name") or value.get("ens_domain_name") or "")
    return str(value or ""),""


def _blockscout_transfers(cfg: ChainConfig, wallet: str) -> list[dict[str,Any]]:
    if not _blockscout_available(cfg):
        return []
    wallet_l=_norm_address(wallet)
    out=[]
    # Native value movements from normal transactions.
    try:
        rows=_blockscout_pages(cfg,f"addresses/{wallet}/transactions")
    except Exception:
        rows=[]
    for idx,row in enumerate(rows):
        frm_raw,_=_addr_value(row.get("from"))
        to_raw,_=_addr_value(row.get("to"))
        frm=_norm_address(frm_raw);to=_norm_address(to_raw)
        direction="SELF" if frm==wallet_l and to==wallet_l else "IN" if to==wallet_l else "OUT" if frm==wallet_l else ""
        amount=max(0.0,_f(row.get("value"))/1e18)
        if not direction or amount<=0:
            continue
        tx=str(row.get("hash") or "")
        unique=f"native:{idx}"
        out.append({
            "id":_event_id(cfg.key,tx,unique,direction,cfg.native_symbol,amount),
            "chain":cfg.key,"tx_hash":tx,"transfer_key":unique,
            "occurred_at":_iso_ts(row.get("timestamp")),"direction":direction,
            "category":"external","asset":cfg.native_symbol,"token_address":"","amount":amount,
            "from_address":frm,"to_address":to,"source":"BLOCKSCOUT",
            "payload":{"block":row.get("block_number") or row.get("block")},
        })
    # ERC-20 token transfers.
    try:
        rows=_blockscout_pages(cfg,f"addresses/{wallet}/token-transfers")
    except Exception:
        rows=[]
    for idx,row in enumerate(rows):
        token=row.get("token") or {}
        symbol=str(token.get("symbol") or "TOKEN")
        if _SUSPICIOUS.search(symbol):
            continue
        frm_raw,_=_addr_value(row.get("from"));to_raw,_=_addr_value(row.get("to"))
        frm=_norm_address(frm_raw);to=_norm_address(to_raw)
        direction="SELF" if frm==wallet_l and to==wallet_l else "IN" if to==wallet_l else "OUT" if frm==wallet_l else ""
        if not direction:
            continue
        total=row.get("total") or {}
        decimals=int(total.get("decimals") or token.get("decimals") or 18)
        raw=total.get("value") if isinstance(total,dict) else row.get("value")
        amount=max(0.0,_f(raw)/(10**decimals))
        if amount<=0:
            continue
        tx=str(row.get("transaction_hash") or row.get("tx_hash") or "")
        address=str(token.get("address") or "")
        unique=str(row.get("log_index") or f"erc20:{idx}:{address}")
        out.append({
            "id":_event_id(cfg.key,tx,unique,direction,symbol,amount),
            "chain":cfg.key,"tx_hash":tx,"transfer_key":unique,
            "occurred_at":_iso_ts(row.get("timestamp")),"direction":direction,
            "category":"erc20","asset":symbol,"token_address":address,"amount":amount,
            "from_address":frm,"to_address":to,"source":"BLOCKSCOUT",
            "payload":{},
        })
    return out


def _decoded_method(row: dict[str,Any]) -> str:
    direct=str(row.get("method") or "").strip()
    decoded=row.get("decoded_input") or {}
    call=str(decoded.get("method_call") or decoded.get("method") or "").strip() if isinstance(decoded,dict) else ""
    return call or direct


def _transaction_kind(cfg: ChainConfig, row: dict[str,Any], method: str, to_address: str, to_name: str, known_event: str = "") -> str:
    if known_event:
        return str(known_event).upper()
    status=str(row.get("status") or row.get("result") or "").lower()
    if status in {"error","failed","reverted","0"}:
        return "FAILED_TRANSACTION"
    m=method.lower();name=to_name.lower();to=_norm_address(to_address)
    if "approve" in m or "setapprovalforall" in m:
        return "APPROVAL"
    if "bridge" in m or "bridge" in name or "deposittransaction" in m or "finalizewithdrawal" in m:
        return "BRIDGE"
    if to==_norm_address(cfg.position_manager) or any(x in m for x in ("increaseliquidity","decreaseliquidity","collect(","mint(")):
        return "LP_ACTION"
    if any(x in m for x in ("swap","exactinput","exactoutput","unoswap")):
        return "SWAP"
    raw_input=str(row.get("raw_input") or row.get("input") or "")
    value=_f(row.get("value"))/1e18
    if value>0 and raw_input in {"","0x","0X"}:
        return "NATIVE_TRANSFER"
    return "CONTRACT_CALL"


def _blockscout_transactions(cfg: ChainConfig, wallet: str, known_events: dict[str,str]) -> list[dict[str,Any]]:
    if not _blockscout_available(cfg):
        return []
    rows=_blockscout_pages(cfg,f"addresses/{wallet}/transactions")
    wallet_l=_norm_address(wallet)
    out=[]
    seen=set()
    for row in rows:
        tx=str(row.get("hash") or "").strip()
        if not tx or tx.lower() in seen:
            continue
        seen.add(tx.lower())
        frm,frm_name=_addr_value(row.get("from"));to,to_name=_addr_value(row.get("to"))
        frm_l=_norm_address(frm);to_l=_norm_address(to)
        direction="SELF" if frm_l==wallet_l and to_l==wallet_l else "OUT" if frm_l==wallet_l else "IN" if to_l==wallet_l else "ASSOCIATED"
        fee=row.get("fee") or {}
        fee_wei=_int_any(fee.get("value") if isinstance(fee,dict) else 0)
        gas_used=_int_any(row.get("gas_used"))
        gas_price=_int_any(row.get("effective_gas_price") or row.get("gas_price"))
        if fee_wei<=0 and gas_used>0 and gas_price>0:
            fee_wei=gas_used*gas_price
        # Only the sending wallet pays gas.
        gas_native=(fee_wei/1e18) if direction in {"OUT","SELF"} else 0.0
        method=_decoded_method(row)
        known=known_events.get(tx.lower(),"")
        kind=_transaction_kind(cfg,row,method,to,to_name,known)
        status=str(row.get("status") or row.get("result") or "").lower()
        success=status not in {"error","failed","reverted","0"}
        out.append({
            "id":_tx_id(cfg.key,tx),
            "chain":cfg.key,
            "tx_hash":tx,
            "occurred_at":_iso_ts(row.get("timestamp")),
            "direction":direction,
            "kind":kind,
            "method":method,
            "from_address":frm,
            "to_address":to,
            "to_name":to_name or frm_name,
            "native_symbol":cfg.native_symbol,
            "value_native":max(0.0,_f(row.get("value"))/1e18),
            "gas_native":max(0.0,gas_native),
            "success":success,
            "source":"BLOCKSCOUT_FULL_HISTORY",
            "payload":{
                "block":row.get("block_number") or row.get("block"),
                "nonce":row.get("nonce"),
                "raw_input":row.get("raw_input"),
                "decoded_input":row.get("decoded_input"),
                "gas_used":gas_used,
                "gas_price":gas_price,
                "fee_wei":fee_wei,
                "blockscout_type":row.get("type"),
                "known_lp_manager_event":known,
            },
        })
    return out


def _rpc_transactions_from_transfer_hashes(cfg: ChainConfig, transfers: list[dict[str,Any]], known_events: dict[str,str]) -> list[dict[str,Any]]:
    url=cfg.rpc_url()
    if not url:
        return []
    wallet_times={str(x.get("tx_hash") or "").lower():_f(x.get("occurred_at")) for x in transfers if x.get("tx_hash")}
    out=[]
    for tx_hash,occurred in list(wallet_times.items())[:500]:
        try:
            tx=_rpc(url,"eth_getTransactionByHash",[tx_hash]) or {}
            receipt=_rpc(url,"eth_getTransactionReceipt",[tx_hash]) or {}
        except Exception:
            continue
        frm=str(tx.get("from") or "");to=str(tx.get("to") or "")
        wallet_l=""
        # Direction is refined by transfer direction below when RPC lacks address context.
        related=[x for x in transfers if str(x.get("tx_hash") or "").lower()==tx_hash]
        dirs={str(x.get("direction") or "") for x in related}
        direction="SELF" if "SELF" in dirs else "OUT" if "OUT" in dirs else "IN" if "IN" in dirs else "ASSOCIATED"
        gas_used=_int_any(receipt.get("gasUsed"))
        gas_price=_int_any(receipt.get("effectiveGasPrice") or tx.get("gasPrice"))
        gas_native=gas_used*gas_price/1e18 if direction in {"OUT","SELF"} else 0.0
        input_hex=str(tx.get("input") or "")
        method=input_hex[:10] if input_hex and input_hex!="0x" else ""
        row={"status":"ok" if _int_any(receipt.get("status"),1)==1 else "error","input":input_hex,"value":str(_int_any(tx.get("value")))}
        kind=_transaction_kind(cfg,row,method,to,"",known_events.get(tx_hash,""))
        out.append({
            "id":_tx_id(cfg.key,tx_hash),"chain":cfg.key,"tx_hash":tx_hash,
            "occurred_at":occurred,"direction":direction,"kind":kind,"method":method,
            "from_address":frm,"to_address":to,"to_name":"","native_symbol":cfg.native_symbol,
            "value_native":_int_any(tx.get("value"))/1e18,"gas_native":gas_native,
            "success":_int_any(receipt.get("status"),1)==1,"source":"RPC_KNOWN_TRANSFER_TXS",
            "payload":{"gas_used":gas_used,"gas_price":gas_price,"input":input_hex,"block":tx.get("blockNumber")},
        })
    return out


def _known_internal_hashes(store) -> set[str]:
    return {
        str(e.get("tx_hash") or "").lower()
        for e in store.list_financial_events(5000)
        if str(e.get("tx_hash") or "").strip()
    }


def _known_event_types(store) -> dict[str,str]:
    return {
        str(e.get("tx_hash") or "").lower():str(e.get("event_type") or "LP_MANAGER_TRANSACTION")
        for e in store.list_financial_events(5000)
        if str(e.get("tx_hash") or "").strip()
    }


def _classify_scan_rows(store, rows: list[dict[str,Any]]) -> list[dict[str,Any]]:
    known=_known_internal_hashes(store)
    directions={}
    for row in rows:
        directions.setdefault(str(row.get("tx_hash") or "").lower(),set()).add(str(row.get("direction") or ""))
    out=[]
    for row in rows:
        tx=str(row.get("tx_hash") or "").lower()
        status="UNRESOLVED"
        if tx and tx in known:
            status=INTERNAL_PROTOCOL
        elif row.get("direction")=="SELF":
            status=INTERNAL_TRANSFER
        elif {"IN","OUT"}.issubset(directions.get(tx,set())):
            status=INTERNAL_CONVERSION
        out.append({**row,"review_status":status})
    return out


def scan_wallet_audit(settings, store, *, chains: list[str] | None = None) -> dict[str,Any]:
    wallet=str(settings.wallet_address or "").strip()
    if not wallet:
        return {"ok":False,"error":"WALLET_ADDRESS is not configured","chains":[],"imported":0,"transactions":0}
    selected=[str(c).upper() for c in (chains or list(CHAINS)) if str(c).upper() in CHAINS and CHAINS[str(c).upper()].enabled()]
    chain_results=[]
    imported=0
    transaction_count=0
    known_events=_known_event_types(store)
    for key in selected:
        cfg=CHAINS[key]
        transfer_rows=[]
        transfer_provider="NONE"
        transfer_error=""
        try:
            if _alchemy_url(cfg):
                transfer_rows=_alchemy_transfers(cfg,wallet)
                transfer_provider="ALCHEMY_ASSET_TRANSFERS"
            elif _blockscout_available(cfg):
                transfer_rows=_blockscout_transfers(cfg,wallet)
                transfer_provider="BLOCKSCOUT"
        except Exception as exc:
            transfer_error=str(exc)[:240]

        transfer_rows=_classify_scan_rows(store,transfer_rows)
        for row in transfer_rows:
            store.upsert_wallet_audit_event(row)
            imported+=1

        tx_rows=[]
        tx_provider="NONE"
        tx_error=""
        full_history=False
        try:
            if _blockscout_available(cfg):
                tx_rows=_blockscout_transactions(cfg,wallet,known_events)
                tx_provider="BLOCKSCOUT_FULL_HISTORY"
                full_history=True
            elif transfer_rows:
                tx_rows=_rpc_transactions_from_transfer_hashes(cfg,transfer_rows,known_events)
                tx_provider="RPC_KNOWN_TRANSFER_TXS"
        except Exception as exc:
            tx_error=str(exc)[:240]
            if transfer_rows:
                try:
                    tx_rows=_rpc_transactions_from_transfer_hashes(cfg,transfer_rows,known_events)
                    tx_provider="RPC_KNOWN_TRANSFER_TXS"
                except Exception:
                    tx_rows=[]

        for row in tx_rows:
            store.upsert_wallet_audit_transaction(row)
            transaction_count+=1

        outgoing=[x for x in tx_rows if str(x.get("direction") or "") in {"OUT","SELF"}]
        gas_native=sum(max(0.0,_f(x.get("gas_native"))) for x in outgoing)
        chain_results.append({
            "chain":key,
            "ok":bool(transfer_rows or tx_rows or (not transfer_error and not tx_error)),
            "transfer_provider":transfer_provider,
            "transaction_provider":tx_provider,
            "transfers":len(transfer_rows),
            "transactions":len(tx_rows),
            "gas_transactions":sum(1 for x in outgoing if _f(x.get("gas_native"))>0),
            "gas_native":gas_native,
            "native_symbol":cfg.native_symbol,
            "full_transaction_history":full_history,
            "transfer_error":transfer_error,
            "transaction_error":tx_error,
        })

    scan={
        "read_at":time.time(),
        "chains":chain_results,
        "imported":imported,
        "transactions":transaction_count,
        "coverage_complete":all((not x.get("transfers") and not x.get("transactions")) or x.get("full_transaction_history") for x in chain_results),
    }
    store.set_setting("wallet_audit:last_scan",scan)
    return {"ok":any(x.get("ok") for x in chain_results),"wallet":wallet,**scan}


def _decorate_transfer_transactions(transfers: list[dict[str,Any]], transactions: list[dict[str,Any]]) -> list[dict[str,Any]]:
    tx_map={(str(t.get("chain") or ""),str(t.get("tx_hash") or "").lower()):t for t in transactions}
    return [
        {**r,"transaction":tx_map.get((str(r.get("chain") or ""),str(r.get("tx_hash") or "").lower()))}
        for r in transfers
    ]


def _full_activity(transfers: list[dict[str,Any]], transactions: list[dict[str,Any]], limit: int = 750) -> list[dict[str,Any]]:
    grouped={}
    for tr in transfers:
        key=(str(tr.get("chain") or ""),str(tr.get("tx_hash") or "").lower())
        grouped.setdefault(key,[]).append(tr)
    seen=set()
    rows=[]
    for tx in transactions:
        key=(str(tx.get("chain") or ""),str(tx.get("tx_hash") or "").lower())
        seen.add(key)
        related=grouped.get(key,[])
        rows.append({
            "activity_type":"TRANSACTION",
            **tx,
            "transfers":[{
                "direction":r.get("direction"),"asset":r.get("asset"),"amount":r.get("amount"),
                "review_status":r.get("review_status"),"token_address":r.get("token_address"),
            } for r in related],
        })
    for key,related in grouped.items():
        if key in seen:
            continue
        occurred=max((_f(r.get("occurred_at")) for r in related),default=0)
        first=related[0]
        rows.append({
            "activity_type":"TRANSFER_ONLY",
            "id":"transfer:"+str(first.get("id") or ""),
            "chain":first.get("chain"),"tx_hash":first.get("tx_hash"),"occurred_at":occurred,
            "direction":first.get("direction"),"kind":"ASSET_TRANSFER","method":"",
            "from_address":first.get("from_address"),"to_address":first.get("to_address"),"to_name":"",
            "native_symbol":"","value_native":0.0,"gas_native":0.0,"success":True,
            "source":first.get("source"),
            "transfers":[{
                "direction":r.get("direction"),"asset":r.get("asset"),"amount":r.get("amount"),
                "review_status":r.get("review_status"),"token_address":r.get("token_address"),
            } for r in related],
        })
    rows.sort(key=lambda x:_f(x.get("occurred_at")),reverse=True)
    return rows[:max(1,int(limit))]


def wallet_audit_summary(settings, store) -> dict[str,Any]:
    raw_rows=store.list_wallet_audit_events(5000)
    tx_rows=store.list_wallet_audit_transactions(5000)
    rows=_decorate_transfer_transactions(raw_rows,tx_rows)
    unresolved=[r for r in rows if str(r.get("review_status") or "")=="UNRESOLVED"]
    contributions=sum(_f(r.get("fiat_amount")) for r in rows if r.get("review_status")==RESOLVED_FUNDING)
    withdrawals=sum(_f(r.get("fiat_amount")) for r in rows if r.get("review_status")==RESOLVED_WITHDRAWAL)
    ctx=money_context(settings,store)
    currency=str(ctx.get("display_currency") or "USD")
    rate=_f(ctx.get("usd_to_display_rate"),1.0)
    snapshot=store.get_wallet_snapshot() or {}
    current_usd=_f(snapshot.get("total_tracked_value_usd"))
    unclaimed_usd=sum(
        max(0.0,_f(p.get("unclaimed_fees")))
        for p in store.list_positions("OPEN")
        if str(p.get("monitoring_class") or "").upper()!="ARCHIVED_SUPERSEDED"
        and str(p.get("source") or "")!="legacy_campaign_ledger"
    )
    current_usd+=unclaimed_usd
    current_display=current_usd*rate
    true_pnl=current_display+withdrawals-contributions

    gas_by_chain={}
    for tx in tx_rows:
        if str(tx.get("direction") or "") not in {"OUT","SELF"}:
            continue
        gas=max(0.0,_f(tx.get("gas_native")))
        if gas<=0:
            continue
        key=str(tx.get("chain") or "UNKNOWN")
        bucket=gas_by_chain.setdefault(key,{"chain":key,"native_symbol":tx.get("native_symbol") or "ETH","gas_native":0.0,"transactions":0})
        bucket["gas_native"]+=gas
        bucket["transactions"]+=1
    for bucket in gas_by_chain.values():
        bucket["gas_native"]=round(bucket["gas_native"],12)

    last_scan=store.get_setting("wallet_audit:last_scan",None)
    full_chains=sum(1 for x in ((last_scan or {}).get("chains") or []) if x.get("full_transaction_history"))
    active_chains=sum(1 for x in ((last_scan or {}).get("chains") or []) if x.get("transfers") or x.get("transactions"))
    return {
        "wallet":str(settings.wallet_address or ""),
        "currency":currency,
        "last_scan":last_scan,
        "metrics":{
            "confirmed_contributions":round(contributions,2),
            "confirmed_withdrawals":round(withdrawals,2),
            "current_portfolio_value":round(current_display,2),
            "provisional_true_pnl":round(true_pnl,2),
            "pnl_complete":len(unresolved)==0 and contributions>0 and bool(last_scan and last_scan.get("coverage_complete")),
            "unresolved_count":len(unresolved),
            "reviewed_count":sum(1 for r in rows if str(r.get("review_status") or "")!="UNRESOLVED"),
            "observed_transfers":len(rows),
            "observed_transactions":len(tx_rows),
            "full_history_chains":full_chains,
            "active_chains":active_chains,
        },
        "gas_by_chain":sorted(gas_by_chain.values(),key=lambda x:x["chain"]),
        "coverage":list((last_scan or {}).get("chains") or []),
        "unresolved":unresolved[:250],
        "events":rows[:500],
        "activity":_full_activity(raw_rows,tx_rows),
        "coverage_note":"Full-history explorer scans capture normal transactions, zero-value contract calls, approvals and gas where supported. Asset transfers remain separately classified for cash-funding evidence. Chains without full-history explorer coverage fall back to known transfer transaction receipts and are explicitly marked partial.",
    }


def resolve_wallet_audit_event(settings, store, event_id: str, *, classification: str, fiat_amount: float | None, note: str = "") -> dict[str,Any]:
    allowed={RESOLVED_FUNDING,RESOLVED_WITHDRAWAL,INTERNAL_TRANSFER,IGNORED}
    status=str(classification or "").upper()
    if status not in allowed:
        raise ValueError("Unsupported wallet-audit classification")
    row=store.get_wallet_audit_event(event_id)
    if not row:
        raise KeyError("Wallet audit event not found")
    currency=str(money_context(settings,store).get("display_currency") or "GBP")
    amount=None
    if status in {RESOLVED_FUNDING,RESOLVED_WITHDRAWAL}:
        amount=max(0.0,_f(fiat_amount))
        if amount<=0:
            raise ValueError("Enter the real cash amount including provider/on-ramp/off-ramp fees")
    return store.resolve_wallet_audit_event(
        event_id,review_status=status,fiat_amount=amount,fiat_currency=currency,note=note,
    ) or {}
