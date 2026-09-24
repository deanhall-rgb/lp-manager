from __future__ import annotations

import json
import math
import time
import re
from dataclasses import dataclass
from typing import Any

from web3 import Web3
import requests

from .chain_registry import ChainConfig
from .market_data import GeckoTerminalClient
from .models import Position
from .rpc_client import build_read_only_web3
from .price_units import display_lens

TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()
UINT128_MAX = 2**128 - 1

OWNER_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ownerOf","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}]
POSITION_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"positions","outputs":[
    {"name":"nonce","type":"uint96"},{"name":"operator","type":"address"},{"name":"token0","type":"address"},{"name":"token1","type":"address"},
    {"name":"fee","type":"uint24"},{"name":"tickLower","type":"int24"},{"name":"tickUpper","type":"int24"},{"name":"liquidity","type":"uint128"},
    {"name":"feeGrowthInside0LastX128","type":"uint256"},{"name":"feeGrowthInside1LastX128","type":"uint256"},
    {"name":"tokensOwed0","type":"uint128"},{"name":"tokensOwed1","type":"uint128"}],"stateMutability":"view","type":"function"}]
FACTORY_ABI = [{"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]
POOL_ABI = [{"inputs":[],"name":"slot0","outputs":[
    {"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},
    {"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"}]
TOKEN_ABI = [
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
]
COLLECT_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"recipient","type":"address"},{"name":"amount0Max","type":"uint128"},{"name":"amount1Max","type":"uint128"}],"name":"params","type":"tuple"}],"name":"collect","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]

STABLES = {"USDC","USDT","DAI","USDS","USDBC","USD+","FRAX","LUSD","GHO"}
ETH_QUOTES = {"WETH","ETH"}


@dataclass
class ScanResult:
    chain: str
    ok: bool
    positions: list[dict[str, Any]]
    scanned_from_block: int | None = None
    latest_block: int | None = None
    discovered_token_ids: int = 0
    owned_nft_api_ids: int = 0
    alchemy_nft_api_ids: int = 0
    transfer_log_ids: int = 0
    discovery_sources: list[str] | None = None
    error: str | None = None


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().replace("0x", "")


def _discover_incoming_token_ids(w3: Web3, manager: str, wallet: str, from_block: int, to_block: int, *, chunk: int = 25_000) -> tuple[set[int], dict[int, dict[str, Any]]]:
    """Discover Uniswap V3 NFTs transferred into the wallet and retain mint/transfer evidence."""
    found: set[int] = set()
    evidence: dict[int, dict[str, Any]] = {}
    topic_wallet = _topic_address(wallet)
    start = from_block
    while start <= to_block:
        end = min(to_block, start + chunk - 1)
        logs = w3.eth.get_logs({"fromBlock": start, "toBlock": end, "address": Web3.to_checksum_address(manager), "topics": [TRANSFER_TOPIC, None, topic_wallet]})
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 4:
                continue
            token_id = int.from_bytes(bytes(topics[3]), "big")
            found.add(token_id)
            block_number = int(log.get("blockNumber") or 0)
            tx_hash = log.get("transactionHash")
            evidence[token_id] = {
                "block_number": block_number,
                "transaction_hash": tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash or ""),
                "discovery_source": "TRANSFER_LOG",
            }
        start = end + 1
    return found, evidence


def _blockscout_nft_items(payload: Any, manager: str) -> set[int]:
    """Parse Blockscout's address NFT response without trusting metadata names/symbols."""
    rows = payload.get("items") or [] if isinstance(payload, dict) else []
    manager_l = str(manager or "").lower()
    out: set[int] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        token = item.get("token") or {}
        contract = str(token.get("address_hash") or token.get("address") or item.get("token_contract_address_hash") or "").lower()
        if contract != manager_l:
            continue
        raw_id = item.get("id") or item.get("token_id")
        if raw_id is None and isinstance(item.get("token_instance"), dict):
            raw_id = item["token_instance"].get("id") or item["token_instance"].get("token_id")
        try:
            out.add(int(str(raw_id), 0))
        except Exception:
            try: out.add(int(str(raw_id)))
            except Exception: pass
    return out


def _discover_owned_token_ids_blockscout(cfg: ChainConfig, wallet: str, *, max_pages: int = 8) -> set[int]:
    """Enumerate currently-owned V3 position NFTs independent of the RPC log checkpoint.

    This is a discovery accelerator/fallback only. Every returned token ID is still
    verified with ownerOf + positions() against the configured chain RPC before import.
    """
    base = str(getattr(cfg, "explorer_api_base", "") or "").rstrip("/")
    if not base:
        return set()
    url = f"{base}/addresses/{wallet}/nft"
    params: dict[str, Any] = {"type": "ERC-721"}
    found: set[int] = set()
    for _ in range(max(1, max_pages)):
        try:
            r = requests.get(url, params=params, timeout=12, headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.4"})
            r.raise_for_status()
            payload = r.json()
        except Exception:
            break
        found.update(_blockscout_nft_items(payload, cfg.position_manager))
        nxt = payload.get("next_page_params") if isinstance(payload, dict) else None
        if not isinstance(nxt, dict) or not nxt:
            break
        params = {"type":"ERC-721", **{str(k):v for k,v in nxt.items() if v is not None}}
    return found


def _discover_owned_token_ids_alchemy(cfg: ChainConfig, wallet: str, *, max_pages: int = 8) -> set[int]:
    """Enumerate current owner NFTs through Alchemy when the product is available.

    This is independent of the primary RPC and of the log checkpoint. Results are
    still verified with ownerOf/positions() on the configured chain RPC.
    """
    import os
    api_key=os.getenv("ALCHEMY_API_KEY", "").strip()
    if not api_key or not cfg.alchemy_slug:
        return set()
    url=f"https://{cfg.alchemy_slug}.g.alchemy.com/nft/v3/{api_key}/getNFTsForOwner"
    found:set[int]=set(); page_key=None
    for _ in range(max(1,max_pages)):
        params=[("owner",wallet),("contractAddresses[]",cfg.position_manager),("withMetadata","false"),("pageSize","100")]
        if page_key: params.append(("pageKey",page_key))
        try:
            r=requests.get(url,params=params,timeout=12,headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.4"})
            r.raise_for_status(); payload=r.json()
        except Exception:
            break
        for row in payload.get("ownedNfts") or []:
            if not isinstance(row,dict): continue
            contract=str((row.get("contract") or {}).get("address") or "").lower()
            if contract and contract != str(cfg.position_manager).lower(): continue
            raw=row.get("tokenId")
            try: found.add(int(str(raw),0))
            except Exception:
                try: found.add(int(str(raw)))
                except Exception: pass
        page_key=payload.get("pageKey")
        if not page_key: break
    return found


def _extract_transfer_ids_from_receipt(receipt: Any, manager: str, wallet: str) -> set[int]:
    """Pure-ish receipt parser used by the operator transaction bootstrap path."""
    manager_l = str(manager or "").lower()
    wallet_topic = _topic_address(wallet).lower()
    out: set[int] = set()
    for log in (receipt.get("logs") or []):
        if str(log.get("address") or "").lower() != manager_l:
            continue
        topics = log.get("topics") or []
        if len(topics) < 4:
            continue
        t0 = topics[0].hex() if hasattr(topics[0], "hex") else str(topics[0])
        t2 = topics[2].hex() if hasattr(topics[2], "hex") else str(topics[2])
        if not t0.startswith("0x"): t0 = "0x" + t0
        if not t2.startswith("0x"): t2 = "0x" + t2
        if t0.lower() != TRANSFER_TOPIC.lower() or t2.lower() != wallet_topic:
            continue
        try: out.add(int.from_bytes(bytes(topics[3]), "big"))
        except Exception:
            try: out.add(int(str(topics[3]), 16))
            except Exception: pass
    return out


def discover_token_ids_from_transactions(cfg: ChainConfig, wallet: str, transaction_hashes: list[str]) -> tuple[set[int], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Resolve opening transaction hashes to owned V3 token IDs without signing anything."""
    if not cfg.rpc_url():
        raise RuntimeError(f"{cfg.rpc_env} not configured")
    if not wallet or not Web3.is_address(wallet):
        raise RuntimeError("WALLET_ADDRESS missing or invalid")
    w3 = build_read_only_web3(cfg.rpc_url())
    ids: set[int] = set(); evidence: dict[int, dict[str, Any]] = {}; results=[]
    for raw in transaction_hashes:
        tx = str(raw or "").strip()
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx):
            results.append({"transaction_hash":tx,"ok":False,"error":"INVALID_TRANSACTION_HASH"}); continue
        try:
            receipt=w3.eth.get_transaction_receipt(tx)
            if int(receipt.get("status") or 0) != 1:
                results.append({"transaction_hash":tx,"ok":False,"error":"TRANSACTION_FAILED"}); continue
            found=_extract_transfer_ids_from_receipt(receipt,cfg.position_manager,wallet)
            block_number=int(receipt.get("blockNumber") or 0)
            opened_at=0.0
            if block_number:
                try: opened_at=float(w3.eth.get_block(block_number).get("timestamp") or 0)
                except Exception: pass
            for token_id in found:
                ids.add(token_id); evidence[token_id]={"block_number":block_number,"transaction_hash":tx,"opened_at":opened_at,"discovery_source":"OPENING_TRANSACTION"}
            results.append({"transaction_hash":tx,"ok":bool(found),"token_ids":sorted(found),"block_number":block_number,"error":None if found else "NO_POSITION_NFT_TRANSFER_TO_CONFIGURED_WALLET"})
        except Exception as exc:
            results.append({"transaction_hash":tx,"ok":False,"error":str(exc)[:240]})
    return ids,evidence,results


def _token_meta(w3: Web3, address: str) -> tuple[str, int]:
    c = w3.eth.contract(address=Web3.to_checksum_address(address), abi=TOKEN_ABI)
    try: symbol = str(c.functions.symbol().call())
    except Exception: symbol = Web3.to_checksum_address(address)[:8]
    try: decimals = int(c.functions.decimals().call())
    except Exception: decimals = 18
    return symbol, decimals


def _raw_price_at_tick(tick: int, dec0: int, dec1: int) -> float:
    # token1 per token0 after decimal adjustment
    try:
        return (1.0001 ** int(tick)) * (10 ** (dec0 - dec1))
    except OverflowError:
        return math.inf if tick > 0 else 0.0


def _amounts(liquidity: int, tick_lower: int, tick_upper: int, current_tick: int, dec0: int, dec1: int) -> tuple[float, float]:
    sa = 1.0001 ** (tick_lower / 2)
    sb = 1.0001 ** (tick_upper / 2)
    sp = 1.0001 ** (current_tick / 2)
    L = float(liquidity)
    if sp <= sa:
        a0, a1 = L * (sb - sa) / (sa * sb), 0.0
    elif sp < sb:
        a0, a1 = L * (sb - sp) / (sp * sb), L * (sp - sa)
    else:
        a0, a1 = 0.0, L * (sb - sa)
    return a0 / (10 ** dec0), a1 / (10 ** dec1)


def _orientation(sym0: str, sym1: str, p_lower: float, p_upper: float, p_current: float) -> tuple[str, float, float, float, bool]:
    # Prefer USD stablecoin as quote; if no stablecoin, prefer WETH/ETH as quote.
    invert = (sym0.upper() in STABLES and sym1.upper() not in STABLES) or (sym0.upper() in ETH_QUOTES and sym1.upper() not in STABLES | ETH_QUOTES)
    if invert:
        vals = [1 / p_upper if p_upper else 0, 1 / p_lower if p_lower else 0, 1 / p_current if p_current else 0]
        return f"{sym1}/{sym0}", vals[0], vals[1], vals[2], True
    return f"{sym0}/{sym1}", p_lower, p_upper, p_current, False


def _pool_market(market: GeckoTerminalClient | None, chain: str, pool: str) -> dict[str, Any]:
    if not market or not pool or int(pool, 16) == 0:
        return {}
    try:
        return market.pool(chain, pool)
    except Exception:
        return {}


def _usd_prices(market: dict, token0: str, token1: str) -> tuple[float, float]:
    base = market.get("base_token") or {}; quote = market.get("quote_token") or {}
    a0, a1 = token0.lower(), token1.lower()
    if str(base.get("address") or "").lower() == a0:
        return float(market.get("base_token_price_usd") or 0), float(market.get("quote_token_price_usd") or 0)
    if str(base.get("address") or "").lower() == a1:
        return float(market.get("quote_token_price_usd") or 0), float(market.get("base_token_price_usd") or 0)
    return 0.0, 0.0


def scan_chain_positions(cfg: ChainConfig, wallet: str, *, scan_blocks: int, market: GeckoTerminalClient | None = None, from_block_override: int | None = None, known_token_ids: set[int] | None = None, seed_token_ids: set[int] | None = None, seed_evidence: dict[int, dict[str, Any]] | None = None) -> ScanResult:
    if not cfg.rpc_url():
        return ScanResult(cfg.key, False, [], error=f"{cfg.rpc_env} not configured")
    if not wallet or not Web3.is_address(wallet):
        return ScanResult(cfg.key, False, [], error="WALLET_ADDRESS missing or invalid")
    try:
        w3 = build_read_only_web3(cfg.rpc_url())
        actual_chain = int(w3.eth.chain_id)
        if actual_chain != cfg.chain_id:
            raise RuntimeError(f"RPC chain id {actual_chain} != expected {cfg.chain_id}")
        latest = int(w3.eth.block_number)
        start = max(0, int(from_block_override)) if from_block_override is not None else max(0, latest - int(scan_blocks))
        token_ids = set(known_token_ids or set())
        token_ids.update(seed_token_ids or set())
        discovery_evidence: dict[int, dict[str, Any]] = dict(seed_evidence or {})
        explorer_ids = _discover_owned_token_ids_blockscout(cfg, Web3.to_checksum_address(wallet))
        alchemy_ids = _discover_owned_token_ids_alchemy(cfg, Web3.to_checksum_address(wallet))
        token_ids.update(explorer_ids)
        token_ids.update(alchemy_ids)
        log_ids: set[int] = set()
        try:
            log_ids, log_evidence = _discover_incoming_token_ids(w3, cfg.position_manager, Web3.to_checksum_address(wallet), start, latest)
            token_ids.update(log_ids)
            for tid, ev in log_evidence.items(): discovery_evidence.setdefault(tid, ev)
        except Exception:
            # Owned-NFT API + known IDs still allow authoritative ownerOf reads if a
            # provider temporarily refuses a broad eth_getLogs range.
            pass
        manager = w3.eth.contract(address=Web3.to_checksum_address(cfg.position_manager), abi=POSITION_ABI + OWNER_ABI + COLLECT_ABI)
        factory = w3.eth.contract(address=Web3.to_checksum_address(cfg.factory), abi=FACTORY_ABI)
        rows: list[dict[str, Any]] = []
        for token_id in sorted(token_ids):
            try:
                owner = manager.functions.ownerOf(token_id).call()
                if str(owner).lower() != wallet.lower():
                    rows.append({"token_id":str(token_id),"not_owned":True,"owner":str(owner)})
                    continue
                pos = manager.functions.positions(token_id).call()
                token0, token1, fee, tick_lower, tick_upper, liquidity = pos[2], pos[3], int(pos[4]), int(pos[5]), int(pos[6]), int(pos[7])
                pool = factory.functions.getPool(token0, token1, fee).call()
                pool_c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=POOL_ABI)
                slot0 = pool_c.functions.slot0().call(); current_tick = int(slot0[1])
                sym0, dec0 = _token_meta(w3, token0); sym1, dec1 = _token_meta(w3, token1)
                p_lo = _raw_price_at_tick(tick_lower, dec0, dec1); p_hi = _raw_price_at_tick(tick_upper, dec0, dec1); p_cur = _raw_price_at_tick(current_tick, dec0, dec1)
                lens=display_lens(sym0,sym1,p_lo,p_hi,p_cur)
                pair=f"{sym0}/{sym1}"; lower=float(lens["lower"]); upper=float(lens["upper"]); current=float(lens["current"]); inverted=bool(lens["inverted"])
                amount0, amount1 = _amounts(liquidity, tick_lower, tick_upper, current_tick, dec0, dec1)
                fee0 = fee1 = 0.0
                try:
                    quoted = manager.functions.collect((token_id, Web3.to_checksum_address(wallet), UINT128_MAX, UINT128_MAX)).call({"from": Web3.to_checksum_address(wallet)})
                    fee0, fee1 = float(quoted[0]) / (10 ** dec0), float(quoted[1]) / (10 ** dec1)
                except Exception:
                    # tokensOwed is a conservative partial fallback when a provider refuses non-view eth_call.
                    fee0, fee1 = float(pos[10]) / (10 ** dec0), float(pos[11]) / (10 ** dec1)
                market_row = _pool_market(market, cfg.key, pool)
                usd0, usd1 = _usd_prices(market_row, token0, token1)
                value_usd = amount0 * usd0 + amount1 * usd1
                fees_usd = fee0 * usd0 + fee1 * usd1
                discovered = discovery_evidence.get(token_id) or {}
                opened_at = float(discovered.get("opened_at") or 0)
                if not opened_at and discovered.get("block_number"):
                    try: opened_at=float(w3.eth.get_block(int(discovered["block_number"])).get("timestamp") or 0)
                    except Exception: pass
                snapshot = {
                    "live": True, "chain_id": cfg.chain_id, "block_number": latest, "read_at": time.time(),
                    "discovery_source": discovered.get("discovery_source") or ("BLOCKSCOUT_OWNED_NFT" if token_id in explorer_ids else "ALCHEMY_OWNED_NFT" if token_id in alchemy_ids else "KNOWN_POSITION"),
                    "opening_transaction_hash": discovered.get("transaction_hash") or "",
                    "opening_block_number": int(discovered.get("block_number") or 0),
                    "opened_at": opened_at,
                    "wallet": Web3.to_checksum_address(wallet), "token_id": str(token_id), "position_manager": cfg.position_manager,
                    "factory": cfg.factory, "pool_address": pool, "fee_tier": fee, "tick_lower": tick_lower, "tick_upper": tick_upper,
                    "current_tick": current_tick, "liquidity": str(liquidity), "token0": {"address": token0, "symbol": sym0, "decimals": dec0, "amount": amount0, "unclaimed": fee0, "price_usd": usd0},
                    "token1": {"address": token1, "symbol": sym1, "decimals": dec1, "amount": amount1, "unclaimed": fee1, "price_usd": usd1},
                    "display_inverted": inverted, "price_lens": lens, "range_unit": lens["unit"], "range_unit_label": lens["unit_label"],
                    "current_value_usd": value_usd, "unclaimed_fees_usd": fees_usd, "market": market_row,
                    "data_quality": "LIVE_CHAIN_PLUS_MARKET" if market_row else "LIVE_CHAIN_NO_USD_MARKET",
                }
                unit_label=str(lens.get("unit_label") or "")
                if " per " in unit_label:
                    quote, base = unit_label.split(" per ",1); pair=f"{base}/{quote}"
                rows.append({"pair": pair, "lower_price": lower, "upper_price": upper, "current_price": current, "current_value": value_usd, "unclaimed_fees": fees_usd, "token_id": str(token_id), "active_liquidity": liquidity > 0, "opened_at": opened_at, "snapshot": snapshot})
            except Exception as exc:
                rows.append({"token_id": str(token_id), "error": str(exc)})
        sources=[]
        if explorer_ids: sources.append("BLOCKSCOUT_OWNED_NFT")
        if alchemy_ids: sources.append("ALCHEMY_OWNED_NFT")
        if log_ids: sources.append("TRANSFER_LOG")
        if known_token_ids: sources.append("PERSISTED_IDS")
        if seed_token_ids: sources.append("OPENING_TRANSACTION")
        return ScanResult(cfg.key, True, rows, start, latest, len(token_ids), len(explorer_ids), len(alchemy_ids), len(log_ids), sources)
    except Exception as exc:
        return ScanResult(cfg.key, False, [], error=str(exc))


def _next_live_display_name(store, pair: str) -> str:
    """Allocate a stable operator-friendly P number without overwriting manual labels."""
    highest=0
    for row in store.list_positions():
        name=str(row.get("display_name") or "")
        for m in re.finditer(r"(?:^|\b)(?:P|LP)\s*(\d+)(?:\b|$)",name,re.I):
            highest=max(highest,int(m.group(1)))
    return f"P{highest+1} · {pair}"

def reconcile_scan(store, result: ScanResult) -> dict[str, Any]:
    imported = errors = 0
    seen: set[str] = set()
    for row in result.positions:
        if row.get("error"):
            errors += 1
            matched=store.find_position_by_token(result.chain,str(row.get("token_id") or ""))
            if matched: seen.add(str(matched.get("id")))
            continue
        if row.get("not_owned"):
            matched=store.find_position_by_token(result.chain,str(row.get("token_id") or ""))
            if matched:
                store.close_position_record(str(matched.get("id")))
                store.update_position_metadata(str(matched.get("id")),lifecycle_stage="CLOSED",notes=(str(matched.get("notes") or "")+"\nClosed by live reconciliation: NFT is no longer owned by configured wallet."))
            continue
        live_id = f"live:{result.chain}:{row['token_id']}"
        matched = store.find_position_by_token(result.chain, str(row["token_id"]))
        position_id = str((matched or {}).get("id") or live_id)
        seen.add(position_id)
        existing = matched or store.get_position(position_id)
        current_value = float(row.get("current_value") or 0)
        # Unknown cost basis must not create fake profit. Preserve any previously imported cost basis.
        capital_value = float(existing.get("capital_value") or 0) if existing else current_value
        if capital_value <= 0: capital_value = current_value
        old_notes = str(existing.get("notes") or "") if existing else ""
        note = old_notes or "Live-chain position; cost basis defaults to first observed value until ledger reconciliation."
        sleeve = (existing or {}).get("strategy_sleeve") or "TACTICAL_CAMPAIGN"
        p = Position(
            id=position_id, protocol="UNISWAP_V3", chain=result.chain, pair=str(row["pair"]), status="OPEN" if row.get("active_liquidity") else "CLOSED",
            lower_price=float(row["lower_price"]), upper_price=float(row["upper_price"]), current_price=float(row["current_price"]),
            capital_value=capital_value, current_value=current_value, unclaimed_fees=float(row.get("unclaimed_fees") or 0),
            fees_today=float((existing or {}).get("fees_today") or 0), fees_7d=float((existing or {}).get("fees_7d") or 0), fees_30d=float((existing or {}).get("fees_30d") or 0),
            realised_fees=float((existing or {}).get("realised_fees") or 0), estimated_il=float((existing or {}).get("estimated_il") or 0), gas_costs=float((existing or {}).get("gas_costs") or 0),
            apr_current=float((existing or {}).get("apr_current") or 0), apr_7d=float((existing or {}).get("apr_7d") or 0), opened_at=float((existing or {}).get("opened_at") or row.get("opened_at") or time.time()),
            token_id=str(row["token_id"]), campaign_id=(existing or {}).get("campaign_id"), source="live_chain", notes=note,
            strategy_sleeve=sleeve, directional_bias=(existing or {}).get("directional_bias") or "NEUTRAL", inventory_intent=(existing or {}).get("inventory_intent") or "BALANCED",
            target_hold_days=float((existing or {}).get("target_hold_days") or (30 if sleeve == "CORE_INCOME" else 3)), monitoring_class=(existing or {}).get("monitoring_class") or ("LOW_TOUCH" if sleeve == "CORE_INCOME" else "ACTIVE"),
            display_name=((existing or {}).get("display_name") if (existing or {}).get("display_name") and str((existing or {}).get("display_name")) != str((existing or {}).get("pair") or "") else _next_live_display_name(store, str(row.get("pair") or "LP position"))),
            campaign_label=(existing or {}).get("campaign_label") or "",
            entry_thesis=(existing or {}).get("entry_thesis") or "",
            exit_goal=(existing or {}).get("exit_goal") or "",
            lifecycle_stage="ACTIVE" if row.get("active_liquidity") else "CLOSED",
            cost_basis_quality=(existing or {}).get("cost_basis_quality") or ("FIRST_OBSERVED" if not matched else "LEGACY_LEDGER"),
            strategy_version=(existing or {}).get("strategy_version") or "v0.8",
            pool_address=str((row.get("snapshot") or {}).get("pool_address") or (existing or {}).get("pool_address") or ""),
            range_unit=str((row.get("snapshot") or {}).get("range_unit") or (existing or {}).get("range_unit") or "TOKEN1_PER_TOKEN0"),
            closed_at=(time.time() if not row.get("active_liquidity") else float((existing or {}).get("closed_at") or 0)),
            reported_net_pnl=float((existing or {}).get("reported_net_pnl") or 0),
            reported_net_pnl_pct=float((existing or {}).get("reported_net_pnl_pct") or 0),
            pnl_quality=str((existing or {}).get("pnl_quality") or "UNKNOWN"),
        )
        store.upsert_position(p)
        store.save_position_snapshot(position_id, row["snapshot"])
        imported += 1
    # Never close an LP merely because one discovery source omitted it. Ownership
    # loss is authoritative only when ownerOf explicitly returns another owner.
    return {"chain": result.chain, "ok": result.ok, "positions": imported, "errors": errors, "discovered_token_ids": result.discovered_token_ids, "owned_nft_api_ids": result.owned_nft_api_ids, "alchemy_nft_api_ids": result.alchemy_nft_api_ids, "transfer_log_ids": result.transfer_log_ids, "discovery_sources": result.discovery_sources or [], "from_block": result.scanned_from_block, "latest_block": result.latest_block, "error": result.error}
