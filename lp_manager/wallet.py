from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from typing import Any

import requests
try:
    from web3 import Web3
except Exception:  # pragma: no cover - live wallet reads require the installed dependency.
    Web3 = None  # type: ignore[assignment]

from .chain_registry import CHAINS, ChainConfig
from .rpc_client import build_read_only_web3

ERC20_ABI = [
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _known_tokens_from_store(store, chain: str) -> set[str]:
    tokens: set[str] = set()
    for position in store.list_positions():
        if str(position.get("chain") or "").upper() != chain:
            continue
        snap = store.get_position_snapshot(str(position.get("id"))) or {}
        for key in ("token0", "token1"):
            address = str((snap.get(key) or {}).get("address") or "")
            if Web3.is_address(address):
                tokens.add(Web3.to_checksum_address(address))
        legacy = snap.get("position_snapshot") or (snap.get("legacy_campaign") or {}).get("position_snapshot") or {}
        for key in ("token0_address", "token1_address"):
            address = str(legacy.get(key) or "")
            if Web3.is_address(address):
                tokens.add(Web3.to_checksum_address(address))
    return tokens


def _alchemy_url(cfg: ChainConfig) -> str:
    """Use Alchemy for token discovery even when a different primary RPC is configured."""
    key=os.getenv("ALCHEMY_API_KEY", "").strip()
    return f"https://{cfg.alchemy_slug}.g.alchemy.com/v2/{key}" if key and cfg.alchemy_slug else ""


def _alchemy_token_addresses(cfg: ChainConfig, wallet: str) -> set[str]:
    rpc_url=_alchemy_url(cfg)
    if not rpc_url:
        return set()
    try:
        response = requests.post(
            rpc_url,
            json={"jsonrpc":"2.0","id":1,"method":"alchemy_getTokenBalances","params":[wallet,"erc20"]},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            return set()
        rows = (payload.get("result") or {}).get("tokenBalances") or []
        out=set()
        for row in rows:
            address=str(row.get("contractAddress") or "")
            raw=str(row.get("tokenBalance") or "0x0")
            try: nonzero=int(raw,16)>0 if raw.lower().startswith("0x") else int(raw)>0
            except Exception: nonzero=False
            if Web3.is_address(address) and nonzero:
                out.add(Web3.to_checksum_address(address))
        return out
    except Exception:
        return set()


def _blockscout_token_addresses(chain: str, wallet: str) -> set[str]:
    """Best-effort Robinhood discovery fallback; balances are re-read from RPC."""
    if str(chain).upper() != "ROBINHOOD_CHAIN":
        return set()
    url=f"https://robinhoodchain.blockscout.com/api/v2/addresses/{wallet}/token-balances"
    try:
        r=requests.get(url,timeout=15,headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.5"})
        r.raise_for_status(); payload=r.json()
        rows=payload if isinstance(payload,list) else (payload.get("items") or payload.get("token_balances") or []) if isinstance(payload,dict) else []
        out=set()
        for row in rows:
            if not isinstance(row,dict): continue
            token=row.get("token") or row
            address=str(token.get("address") or token.get("contract_address") or row.get("token_address") or "")
            if Web3.is_address(address): out.add(Web3.to_checksum_address(address))
        return out
    except Exception:
        return set()


def rpc_health(cfg: ChainConfig) -> dict[str, Any]:
    url = cfg.rpc_url()
    base = {"chain": cfg.key, "name": cfg.name, "enabled": cfg.enabled(), "rpc_source": cfg.rpc_source(), "ok": False}
    if not cfg.enabled(): return {**base, "status":"DISABLED"}
    if not url: return {**base, "status":"MISSING", "error":f"{cfg.rpc_env} not configured"}
    started=time.perf_counter()
    try:
        w3=build_read_only_web3(url); chain_id=int(w3.eth.chain_id); block=int(w3.eth.block_number)
        latency=(time.perf_counter()-started)*1000.0
        if chain_id != cfg.chain_id:
            return {**base,"status":"WRONG_CHAIN","chain_id":chain_id,"expected_chain_id":cfg.chain_id,"latency_ms":round(latency,1)}
        return {**base,"ok":True,"status":"CONNECTED","chain_id":chain_id,"block_number":block,"latency_ms":round(latency,1)}
    except Exception as exc:
        return {**base,"status":"ERROR","error":str(exc)[:240],"latency_ms":round((time.perf_counter()-started)*1000.0,1)}


def _token_holding(w3: Web3, wallet: str, chain: str, address: str) -> dict[str, Any] | None:
    try:
        address=Web3.to_checksum_address(address)
        token=w3.eth.contract(address=address,abi=ERC20_ABI)
        raw=int(token.functions.balanceOf(Web3.to_checksum_address(wallet)).call())
        if raw <= 0: return None
        try: symbol=str(token.functions.symbol().call()).strip() or "TOKEN"
        except Exception: symbol="TOKEN"
        try: decimals=int(token.functions.decimals().call())
        except Exception: decimals=18
        balance=raw/(10**decimals)
        return {"chain":chain,"type":"ERC20","symbol":symbol,"address":address,"decimals":decimals,"balance":balance,"price_usd":0.0,"value_usd":0.0,"data_quality":"LIVE_BALANCE_NO_PRICE"}
    except Exception:
        return None


def _batch_prices(market, chain: str, addresses: list[str]) -> dict[str,float]:
    if not market or not addresses: return {}
    try:
        if hasattr(market,"token_prices"):
            return market.token_prices(chain, addresses)
    except Exception:
        return {}
    return {}


def _spam_reason(row: dict[str,Any], *, min_visible_value: float) -> str | None:
    symbol=str(row.get("symbol") or "")
    # Common unsolicited-token naming tricks. Legitimate unknown-price tokens are
    # kept visible unless their metadata itself looks promotional/malicious.
    if re.search(r"(?i)(https?://|www\.|\.top\b|\.club\b|\bclaim\b|airdrop|reward|voucher)",symbol):
        return "SUSPICIOUS_TOKEN_NAME"
    price=_safe_float(row.get("price_usd")); value=_safe_float(row.get("value_usd"))
    # A positive ERC-20 balance without a trustworthy live price is useful as
    # diagnostics, but not as portfolio capital. Keep it in the hidden drawer
    # until pricing succeeds rather than showing an unvalued token as an asset.
    if str(row.get("type") or "").upper()=="ERC20" and price <= 0:
        return "NO_LIVE_PRICE"
    if price>0 and value < min_visible_value:
        return f"VALUE_BELOW_{min_visible_value:g}_USD"
    return None


def build_wallet_snapshot(settings, store, market=None, *, include_health: bool = True) -> dict[str, Any]:
    wallet=str(settings.wallet_address or "")
    if not Web3.is_address(wallet):
        return {"ok":False,"wallet":wallet,"error":"Configure a valid WALLET_ADDRESS","holdings":[],"chains":[]}
    wallet=Web3.to_checksum_address(wallet)
    holdings:list[dict[str,Any]]=[]; hidden:list[dict[str,Any]]=[]; chains:list[dict[str,Any]]=[]
    min_visible=max(0.0,_safe_float(os.getenv("LP_MANAGER_WALLET_MIN_VISIBLE_USD","1"),1.0))

    for cfg in CHAINS.values():
        if not cfg.enabled():
            chains.append({"chain":cfg.key,"name":cfg.name,"status":"DISABLED","ok":False,"rpc_source":cfg.rpc_source()}); continue
        url=cfg.rpc_url()
        if not url:
            chains.append({"chain":cfg.key,"name":cfg.name,"status":"MISSING","ok":False,"rpc_source":cfg.rpc_source()}); continue
        health=rpc_health(cfg) if include_health else {"chain":cfg.key,"name":cfg.name,"ok":True,"status":"CONFIGURED","rpc_source":cfg.rpc_source()}
        chains.append(health)
        if include_health and not health.get("ok"): continue
        try:
            w3=build_read_only_web3(url)
            native=float(w3.from_wei(w3.eth.get_balance(wallet),"ether"))
            known=_known_tokens_from_store(store,cfg.key)
            alchemy=_alchemy_token_addresses(cfg,wallet)
            blockscout=_blockscout_token_addresses(cfg.key,wallet)
            token_addresses=known | alchemy | blockscout
            chain_rows=[]
            for address in sorted(token_addresses):
                row=_token_holding(w3,wallet,cfg.key,address)
                if row:
                    row["discovery_source"]="ALCHEMY" if address in alchemy else "BLOCKSCOUT" if address in blockscout else "KNOWN_POSITION"
                    chain_rows.append(row)

            price_addresses=[r["address"] for r in chain_rows if r.get("address")]
            if cfg.wrapped_native: price_addresses.append(cfg.wrapped_native)
            prices=_batch_prices(market,cfg.key,price_addresses)
            native_price=_safe_float(prices.get(str(cfg.wrapped_native).lower())) if cfg.wrapped_native else 0.0
            if native>0:
                holdings.append({"chain":cfg.key,"type":"NATIVE","symbol":cfg.native_symbol,"address":None,"balance":native,"price_usd":native_price,"value_usd":native*native_price,"data_quality":"LIVE_BALANCE_AND_PRICE" if native_price else "LIVE_BALANCE_NO_PRICE","discovery_source":"NATIVE"})
            for row in chain_rows:
                price=_safe_float(prices.get(str(row.get("address") or "").lower()))
                row["price_usd"]=price; row["value_usd"]=_safe_float(row.get("balance"))*price
                row["data_quality"]="LIVE_BALANCE_AND_PRICE" if price else "LIVE_BALANCE_NO_PRICE"
                reason=_spam_reason(row,min_visible_value=min_visible)
                if reason:
                    hidden.append({**row,"hidden_reason":reason})
                else:
                    holdings.append(row)
        except Exception as exc:
            for chain_row in chains:
                if chain_row.get("chain")==cfg.key:
                    chain_row["wallet_error"]=str(exc)[:240]; break

    by_symbol=defaultdict(lambda:{"balance":0.0,"value_usd":0.0,"chains":set()})
    for h in holdings:
        b=by_symbol[h["symbol"]]; b["balance"]+=_safe_float(h.get("balance")); b["value_usd"]+=_safe_float(h.get("value_usd")); b["chains"].add(h["chain"])
    assets=[{"symbol":symbol,"balance":row["balance"],"value_usd":row["value_usd"],"chains":sorted(row["chains"])} for symbol,row in by_symbol.items()]
    assets.sort(key=lambda r:r["value_usd"],reverse=True)
    lp_value=sum(_safe_float(p.get("current_value")) for p in store.list_positions("OPEN") if str(p.get("source") or "") == "live_chain")
    wallet_liquid=sum(_safe_float(h.get("value_usd")) for h in holdings)
    snapshot={
        "ok":True,"wallet":wallet,"read_at":time.time(),"chains":chains,"holdings":holdings,"hidden_holdings":hidden,"assets":assets,
        "hidden_count":len(hidden),"min_visible_value_usd":min_visible,
        "wallet_liquid_value_usd":wallet_liquid,"lp_value_usd":lp_value,"total_tracked_value_usd":wallet_liquid+lp_value,
        "coverage":"ALCHEMY_FULL_ERC20_DISCOVERY_WHEN_KEY_CONFIGURED; ROBINHOOD_BLOCKSCOUT_FALLBACK; BATCHED_GECKOTERMINAL_PRICES; SPAM_AND_DUST_HIDDEN_BY_DEFAULT",
    }
    store.save_wallet_snapshot(snapshot)
    return snapshot
