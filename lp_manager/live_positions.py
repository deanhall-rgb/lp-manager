from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

from web3 import Web3

from .chain_registry import ChainConfig
from .market_data import GeckoTerminalClient
from .models import Position
from .rpc_client import build_read_only_web3

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
    error: str | None = None


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().replace("0x", "")


def _discover_incoming_token_ids(w3: Web3, manager: str, wallet: str, from_block: int, to_block: int, *, chunk: int = 25_000) -> set[int]:
    found: set[int] = set()
    topic_wallet = _topic_address(wallet)
    start = from_block
    while start <= to_block:
        end = min(to_block, start + chunk - 1)
        logs = w3.eth.get_logs({"fromBlock": start, "toBlock": end, "address": Web3.to_checksum_address(manager), "topics": [TRANSFER_TOPIC, None, topic_wallet]})
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) >= 4:
                found.add(int.from_bytes(bytes(topics[3]), "big"))
        start = end + 1
    return found


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


def scan_chain_positions(cfg: ChainConfig, wallet: str, *, scan_blocks: int, market: GeckoTerminalClient | None = None, from_block_override: int | None = None, known_token_ids: set[int] | None = None) -> ScanResult:
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
        token_ids.update(_discover_incoming_token_ids(w3, cfg.position_manager, Web3.to_checksum_address(wallet), start, latest))
        manager = w3.eth.contract(address=Web3.to_checksum_address(cfg.position_manager), abi=POSITION_ABI + OWNER_ABI + COLLECT_ABI)
        factory = w3.eth.contract(address=Web3.to_checksum_address(cfg.factory), abi=FACTORY_ABI)
        rows: list[dict[str, Any]] = []
        for token_id in sorted(token_ids):
            try:
                owner = manager.functions.ownerOf(token_id).call()
                if str(owner).lower() != wallet.lower():
                    continue
                pos = manager.functions.positions(token_id).call()
                token0, token1, fee, tick_lower, tick_upper, liquidity = pos[2], pos[3], int(pos[4]), int(pos[5]), int(pos[6]), int(pos[7])
                pool = factory.functions.getPool(token0, token1, fee).call()
                pool_c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=POOL_ABI)
                slot0 = pool_c.functions.slot0().call(); current_tick = int(slot0[1])
                sym0, dec0 = _token_meta(w3, token0); sym1, dec1 = _token_meta(w3, token1)
                p_lo = _raw_price_at_tick(tick_lower, dec0, dec1); p_hi = _raw_price_at_tick(tick_upper, dec0, dec1); p_cur = _raw_price_at_tick(current_tick, dec0, dec1)
                pair, lower, upper, current, inverted = _orientation(sym0, sym1, p_lo, p_hi, p_cur)
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
                snapshot = {
                    "live": True, "chain_id": cfg.chain_id, "block_number": latest, "read_at": time.time(),
                    "wallet": Web3.to_checksum_address(wallet), "token_id": str(token_id), "position_manager": cfg.position_manager,
                    "factory": cfg.factory, "pool_address": pool, "fee_tier": fee, "tick_lower": tick_lower, "tick_upper": tick_upper,
                    "current_tick": current_tick, "liquidity": str(liquidity), "token0": {"address": token0, "symbol": sym0, "decimals": dec0, "amount": amount0, "unclaimed": fee0, "price_usd": usd0},
                    "token1": {"address": token1, "symbol": sym1, "decimals": dec1, "amount": amount1, "unclaimed": fee1, "price_usd": usd1},
                    "display_inverted": inverted, "current_value_usd": value_usd, "unclaimed_fees_usd": fees_usd, "market": market_row,
                    "data_quality": "LIVE_CHAIN_PLUS_MARKET" if market_row else "LIVE_CHAIN_NO_USD_MARKET",
                }
                rows.append({"pair": pair, "lower_price": lower, "upper_price": upper, "current_price": current, "current_value": value_usd, "unclaimed_fees": fees_usd, "token_id": str(token_id), "active_liquidity": liquidity > 0, "snapshot": snapshot})
            except Exception as exc:
                rows.append({"token_id": str(token_id), "error": str(exc)})
        return ScanResult(cfg.key, True, rows, start, latest, len(token_ids))
    except Exception as exc:
        return ScanResult(cfg.key, False, [], error=str(exc))


def reconcile_scan(store, result: ScanResult) -> dict[str, Any]:
    imported = errors = 0
    seen: set[str] = set()
    for row in result.positions:
        if row.get("error"):
            errors += 1; continue
        position_id = f"live:{result.chain}:{row['token_id']}"
        seen.add(position_id)
        existing = store.get_position(position_id)
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
            apr_current=float((existing or {}).get("apr_current") or 0), apr_7d=float((existing or {}).get("apr_7d") or 0), opened_at=float((existing or {}).get("opened_at") or time.time()),
            token_id=str(row["token_id"]), campaign_id=(existing or {}).get("campaign_id"), source="live_chain", notes=note,
            strategy_sleeve=sleeve, directional_bias=(existing or {}).get("directional_bias") or "NEUTRAL", inventory_intent=(existing or {}).get("inventory_intent") or "BALANCED",
            target_hold_days=float((existing or {}).get("target_hold_days") or (30 if sleeve == "CORE_INCOME" else 3)), monitoring_class=(existing or {}).get("monitoring_class") or ("LOW_TOUCH" if sleeve == "CORE_INCOME" else "ACTIVE"),
        )
        store.upsert_position(p)
        store.save_position_snapshot(position_id, row["snapshot"])
        imported += 1
    if result.ok:
        store.close_missing_live_positions(result.chain, seen)
    return {"chain": result.chain, "ok": result.ok, "positions": imported, "errors": errors, "discovered_token_ids": result.discovered_token_ids, "from_block": result.scanned_from_block, "latest_block": result.latest_block, "error": result.error}
