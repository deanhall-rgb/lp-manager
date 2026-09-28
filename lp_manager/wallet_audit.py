from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

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


def _alchemy_url(cfg: ChainConfig) -> str:
    key=os.getenv("ALCHEMY_API_KEY","").strip()
    return f"https://{cfg.alchemy_slug}.g.alchemy.com/v2/{key}" if key and cfg.alchemy_slug else ""


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


def _blockscout_pages(base: str, path: str, *, max_pages: int = 20) -> list[dict[str,Any]]:
    rows=[]
    params={}
    for _ in range(max_pages):
        url=base.rstrip("/")+"/"+path.lstrip("/")
        r=requests.get(url,params=params,timeout=30,headers={"Accept":"application/json","User-Agent":"LP-Manager/0.9.5"})
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


def _blockscout_transfers(cfg: ChainConfig, wallet: str) -> list[dict[str,Any]]:
    if not cfg.explorer_api_base:
        return []
    wallet_l=_norm_address(wallet)
    out=[]
    # Native transactions.
    try:
        rows=_blockscout_pages(cfg.explorer_api_base,f"addresses/{wallet}/transactions")
    except Exception:
        rows=[]
    for idx,row in enumerate(rows):
        frm=_norm_address((row.get("from") or {}).get("hash") if isinstance(row.get("from"),dict) else row.get("from"))
        to=_norm_address((row.get("to") or {}).get("hash") if isinstance(row.get("to"),dict) else row.get("to"))
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
            "payload":{"block":row.get("block_number")},
        })
    # ERC-20 token transfers.
    try:
        rows=_blockscout_pages(cfg.explorer_api_base,f"addresses/{wallet}/token-transfers")
    except Exception:
        rows=[]
    for idx,row in enumerate(rows):
        token=row.get("token") or {}
        symbol=str(token.get("symbol") or "TOKEN")
        if _SUSPICIOUS.search(symbol):
            continue
        frm_raw=row.get("from") or {};to_raw=row.get("to") or {}
        frm=_norm_address(frm_raw.get("hash") if isinstance(frm_raw,dict) else frm_raw)
        to=_norm_address(to_raw.get("hash") if isinstance(to_raw,dict) else to_raw)
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


def _known_internal_hashes(store) -> set[str]:
    return {
        str(e.get("tx_hash") or "").lower()
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
        return {"ok":False,"error":"WALLET_ADDRESS is not configured","chains":[],"imported":0}
    selected=[str(c).upper() for c in (chains or list(CHAINS)) if str(c).upper() in CHAINS and CHAINS[str(c).upper()].enabled()]
    chain_results=[]
    imported=0
    for key in selected:
        cfg=CHAINS[key]
        try:
            if _alchemy_url(cfg):
                rows=_alchemy_transfers(cfg,wallet)
                provider="ALCHEMY_ASSET_TRANSFERS"
            elif cfg.explorer_api_base:
                rows=_blockscout_transfers(cfg,wallet)
                provider="BLOCKSCOUT"
            else:
                chain_results.append({"chain":key,"ok":False,"provider":"NONE","error":"No historical transfer provider configured","transfers":0})
                continue
            rows=_classify_scan_rows(store,rows)
            for row in rows:
                store.upsert_wallet_audit_event(row)
                imported+=1
            chain_results.append({"chain":key,"ok":True,"provider":provider,"transfers":len(rows)})
        except Exception as exc:
            chain_results.append({"chain":key,"ok":False,"provider":"ERROR","error":str(exc)[:240],"transfers":0})
    store.set_setting("wallet_audit:last_scan",{"read_at":time.time(),"chains":chain_results,"imported":imported})
    return {"ok":any(x.get("ok") for x in chain_results),"wallet":wallet,"chains":chain_results,"imported":imported}


def wallet_audit_summary(settings, store) -> dict[str,Any]:
    rows=store.list_wallet_audit_events(5000)
    unresolved=[r for r in rows if str(r.get("review_status") or "")=="UNRESOLVED"]
    contributions=sum(_f(r.get("fiat_amount")) for r in rows if r.get("review_status")==RESOLVED_FUNDING)
    withdrawals=sum(_f(r.get("fiat_amount")) for r in rows if r.get("review_status")==RESOLVED_WITHDRAWAL)
    ctx=money_context(settings,store)
    currency=str(ctx.get("display_currency") or "USD")
    rate=_f(ctx.get("usd_to_display_rate"),1.0)
    snapshot=store.get_wallet_snapshot() or {}
    current_usd=_f(snapshot.get("total_tracked_value_usd"))
    current_display=current_usd*rate
    true_pnl=current_display+withdrawals-contributions
    return {
        "wallet":str(settings.wallet_address or ""),
        "currency":currency,
        "last_scan":store.get_setting("wallet_audit:last_scan",None),
        "metrics":{
            "confirmed_contributions":round(contributions,2),
            "confirmed_withdrawals":round(withdrawals,2),
            "current_portfolio_value":round(current_display,2),
            "provisional_true_pnl":round(true_pnl,2),
            "pnl_complete":len(unresolved)==0 and contributions>0,
            "unresolved_count":len(unresolved),
            "reviewed_count":sum(1 for r in rows if str(r.get("review_status") or "")!="UNRESOLVED"),
            "observed_transfers":len(rows),
        },
        "unresolved":unresolved[:250],
        "events":rows[:500],
        "coverage_note":"Inbound/outbound chain transfers are discovered automatically where a historical provider is configured. One-sided transfers remain unresolved until you classify them; swaps/protocol transactions with both directions or known LP Manager receipts are marked internal automatically.",
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
