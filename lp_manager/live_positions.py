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
from .fee_metrics import observed_fee_metrics
from .position_identity import authoritative_label
from .v3_math import Q96, tick_to_sqrt_price_x96

TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().removeprefix("0x")
INCREASE_LIQUIDITY_TOPIC = "0x" + Web3.keccak(text="IncreaseLiquidity(uint256,uint128,uint256,uint256)").hex().removeprefix("0x")
DECREASE_LIQUIDITY_TOPIC = "0x" + Web3.keccak(text="DecreaseLiquidity(uint256,uint128,uint256,uint256)").hex().removeprefix("0x")
COLLECT_TOPIC = "0x" + Web3.keccak(text="Collect(uint256,address,uint256,uint256)").hex().removeprefix("0x")
UINT128_MAX = 2**128 - 1

OWNER_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ownerOf","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}]
POSITION_ABI = [{"inputs":[{"name":"tokenId","type":"uint256"}],"name":"positions","outputs":[
    {"name":"nonce","type":"uint96"},{"name":"operator","type":"address"},{"name":"token0","type":"address"},{"name":"token1","type":"address"},
    {"name":"fee","type":"uint24"},{"name":"tickLower","type":"int24"},{"name":"tickUpper","type":"int24"},{"name":"liquidity","type":"uint128"},
    {"name":"feeGrowthInside0LastX128","type":"uint256"},{"name":"feeGrowthInside1LastX128","type":"uint256"},
    {"name":"tokensOwed0","type":"uint128"},{"name":"tokensOwed1","type":"uint128"}],"stateMutability":"view","type":"function"}]
FACTORY_ABI = [{"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]
POOL_ABI = [
    {"inputs":[],"name":"slot0","outputs":[
        {"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},
        {"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"feeGrowthGlobal0X128","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"feeGrowthGlobal1X128","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"","type":"int24"}],"name":"ticks","outputs":[
        {"name":"liquidityGross","type":"uint128"},{"name":"liquidityNet","type":"int128"},
        {"name":"feeGrowthOutside0X128","type":"uint256"},{"name":"feeGrowthOutside1X128","type":"uint256"},
        {"name":"tickCumulativeOutside","type":"int56"},{"name":"secondsPerLiquidityOutsideX128","type":"uint160"},
        {"name":"secondsOutside","type":"uint32"},{"name":"initialized","type":"bool"}],
        "stateMutability":"view","type":"function"},
]
TOKEN_ABI = [
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
]
COLLECT_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"recipient","type":"address"},{"name":"amount0Max","type":"uint128"},{"name":"amount1Max","type":"uint128"}],"name":"params","type":"tuple"}],"name":"collect","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]

STABLES = {"USDC","USDT","USDG","DAI","USDS","USDBC","USD+","FRAX","LUSD","GHO"}
ETH_QUOTES = {"WETH","ETH"}
MAJORS = {"WETH","ETH","WBTC","BTC"}


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
            r = requests.get(url, params=params, timeout=12, headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.5"})
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
            r=requests.get(url,params=params,timeout=12,headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.5"})
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




def _parse_iso_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        if isinstance(value, (int, float)):
            return float(value)
        from datetime import datetime
        text = str(value).strip().replace("Z", "+00:00")
        return float(datetime.fromisoformat(text).timestamp())
    except Exception:
        return 0.0


def _address_hash(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("hash") or value.get("address") or "").lower()
    return str(value or "").lower()


def _discover_opening_evidence_blockscout(cfg: ChainConfig, wallet: str, token_id: int) -> dict[str, Any]:
    """Find the mint/incoming transfer for one currently-owned V3 NFT.

    Ownership APIs intentionally answer only *what is owned now*.  This lookup is
    used once per NFT so `opened_at` means the on-chain mint/transfer time rather
    than the first LP Manager scan.
    """
    base = str(getattr(cfg, "explorer_api_base", "") or "").rstrip("/")
    if not base:
        return {}
    url = f"{base}/tokens/{cfg.position_manager}/instances/{int(token_id)}/transfers"
    try:
        r = requests.get(url, timeout=12, headers={"Accept":"application/json","User-Agent":"LP-Manager/0.8.5"})
        r.raise_for_status(); payload = r.json()
    except Exception:
        return {}
    wallet_l = str(wallet).lower()
    candidates=[]
    for row in (payload.get("items") or [] if isinstance(payload,dict) else []):
        if not isinstance(row,dict):
            continue
        to_addr=_address_hash(row.get("to"))
        if to_addr and to_addr != wallet_l:
            continue
        block=int(row.get("block_number") or 0)
        tx=str(row.get("transaction_hash") or ((row.get("transaction") or {}).get("hash") if isinstance(row.get("transaction"),dict) else "") or "")
        ts=_parse_iso_timestamp(row.get("timestamp"))
        from_addr=_address_hash(row.get("from"))
        candidates.append((block or 10**20, ts or 10**20, {
            "block_number": block, "transaction_hash": tx, "opened_at": 0.0 if ts == 10**20 else ts,
            "from": from_addr, "discovery_source": "BLOCKSCOUT_NFT_TRANSFER",
        }))
    if not candidates:
        return {}
    candidates.sort(key=lambda x:(x[0],x[1]))
    return candidates[0][2]


def _hex_topic(value: Any) -> str:
    if hasattr(value, "hex"):
        value=value.hex()
    text=str(value or "")
    return text if text.startswith("0x") else "0x"+text


def _erc20_deposits_from_receipt(receipt: Any, *, pool: str, token0: str, token1: str, dec0: int, dec1: int) -> tuple[float,float]:
    """Sum ERC-20 Transfer events into the pool in the opening transaction."""
    pool_topic=_topic_address(pool).lower(); transfer=TRANSFER_TOPIC.lower()
    totals={str(token0).lower():0, str(token1).lower():0}
    for log in (receipt.get("logs") or []):
        addr=str(log.get("address") or "").lower()
        if addr not in totals:
            continue
        topics=log.get("topics") or []
        if len(topics) != 3:
            continue
        if _hex_topic(topics[0]).lower() != transfer or _hex_topic(topics[2]).lower() != pool_topic:
            continue
        raw=log.get("data")
        try:
            if isinstance(raw,(bytes,bytearray)) or hasattr(raw,"hex"):
                amount=int.from_bytes(bytes(raw),"big")
            else:
                amount=int(str(raw),16)
            totals[addr]+=amount
        except Exception:
            continue
    return totals[str(token0).lower()]/(10**int(dec0)), totals[str(token1).lower()]/(10**int(dec1))


def _opening_liquidity_event(
    receipt: Any, *, manager: str, token_id: int, tick_lower: int, tick_upper: int,
    dec0: int, dec1: int,
) -> dict[str, Any]:
    """Decode the NFT manager IncreaseLiquidity event from the opening receipt.

    This gives the exact liquidity and token amounts actually accepted by the mint.
    From L, amount0/amount1 and the position ticks we can reconstruct the opening
    sqrt price even when the RPC cannot serve historical slot0 state.
    """
    manager_l=str(manager or "").lower()
    topic0=INCREASE_LIQUIDITY_TOPIC.lower()
    for log in receipt.get("logs") or []:
        if str(log.get("address") or "").lower()!=manager_l:
            continue
        topics=log.get("topics") or []
        if len(topics)<2 or _hex_topic(topics[0]).lower()!=topic0:
            continue
        try:
            event_token=int(_hex_topic(topics[1]),16)
        except Exception:
            continue
        if int(event_token)!=int(token_id):
            continue
        raw=log.get("data")
        if isinstance(raw,(bytes,bytearray)) or hasattr(raw,"hex"):
            data=bytes(raw)
        else:
            text=str(raw or "0x").removeprefix("0x")
            data=bytes.fromhex(text)
        if len(data)<96:
            continue
        liquidity=int.from_bytes(data[0:32],"big")
        amount0_raw=int.from_bytes(data[32:64],"big")
        amount1_raw=int.from_bytes(data[64:96],"big")
        if liquidity<=0:
            continue
        sqrt_a=tick_to_sqrt_price_x96(int(tick_lower))
        sqrt_b=tick_to_sqrt_price_x96(int(tick_upper))
        if sqrt_a>sqrt_b:
            sqrt_a,sqrt_b=sqrt_b,sqrt_a
        candidates=[]
        if amount1_raw>0:
            p1=sqrt_a+(float(amount1_raw)*Q96/float(liquidity))
            if sqrt_a<=p1<=sqrt_b:
                candidates.append(p1)
        if amount0_raw>0:
            denom=float(amount0_raw)*sqrt_b+float(liquidity)*Q96
            if denom>0:
                p0=float(liquidity)*Q96*sqrt_b/denom
                if sqrt_a<=p0<=sqrt_b:
                    candidates.append(p0)
        sqrt_p=sum(candidates)/len(candidates) if candidates else 0.0
        tick=None
        if sqrt_p>0:
            try:
                tick=int(round(2.0*math.log(sqrt_p/Q96)/math.log(1.0001)))
            except Exception:
                tick=None
        return {
            "liquidity":liquidity,
            "amount0_raw":amount0_raw,
            "amount1_raw":amount1_raw,
            "amount0":amount0_raw/(10**int(dec0)),
            "amount1":amount1_raw/(10**int(dec1)),
            "sqrt_price_x96":sqrt_p,
            "reconstructed_tick":tick,
            "price_source":"OPENING_INCREASE_LIQUIDITY_EVENT",
        }
    return {}


def _auto_sleeve(pair: str) -> str:
    symbols={x.strip().upper() for x in str(pair or "").replace("-","/").split("/") if x.strip()}
    if len(symbols)>=2 and symbols.issubset(STABLES):
        return "CORE_INCOME"
    if symbols & MAJORS and symbols & STABLES:
        return "CORE_INCOME"
    return "TACTICAL_CAMPAIGN"


def _live_display_name(store, token_id: str, pair: str, chain: str = "ROBINHOOD_CHAIN") -> str:
    """Stable operator labels, with immutable P4/P5/P6 NFT identity."""
    key="live:auto-labels:v087"
    mapping=store.get_setting(key,{}) or {}
    tid=str(token_id)
    authoritative=authoritative_label(chain,tid)
    if authoritative:
        if mapping.get(tid) != authoritative:
            mapping[tid]=authoritative
            store.set_setting(key,mapping)
        return f"{authoritative} · {pair}"
    if tid not in mapping:
        used=[]
        for value in mapping.values():
            m=re.match(r"P(\d+)$",str(value or ""),re.I)
            if m:
                used.append(int(m.group(1)))
        next_no=max([3,*used])+1
        mapping[tid]=f"P{next_no}"
        store.set_setting(key,mapping)
    return f"{mapping[tid]} · {pair}"


def _entry_basis_complete(entry: dict[str, Any] | None) -> bool:
    """True only when every deposited token has a USD mark at the opening time."""
    entry=entry or {}
    if bool(entry.get("basis_complete")):
        return True
    a0=max(0.0,float(entry.get("token0_amount") or 0))
    a1=max(0.0,float(entry.get("token1_amount") or 0))
    p0=max(0.0,float(entry.get("token0_price_usd") or 0))
    p1=max(0.0,float(entry.get("token1_price_usd") or 0))
    if a0<=0 and a1<=0:
        return False
    return (a0<=0 or p0>0) and (a1<=0 or p1>0) and float(entry.get("entry_value_usd") or 0)>0


def _u256_sub(a: int, b: int) -> int:
    return (int(a) - int(b)) % (1 << 256)


def _live_v3_fees_from_growth(
    pool_c: Any, pos: Any, current_tick: int, tick_lower: int, tick_upper: int, liquidity: int,
    dec0: int, dec1: int,
) -> tuple[float,float,str]:
    """Calculate current claimable V3 fees directly from pool fee-growth state.

    NonfungiblePositionManager.positions().tokensOwed is *stored* state and can
    remain unchanged for long periods. This formula is the same economic state a
    zero-liquidity burn/collect simulation would realise, but it uses view calls
    only and therefore does not depend on a provider allowing payable eth_call.
    """
    if int(liquidity) < 0:
        raise ValueError("invalid liquidity")
    global0=int(pool_c.functions.feeGrowthGlobal0X128().call())
    global1=int(pool_c.functions.feeGrowthGlobal1X128().call())
    lower=pool_c.functions.ticks(int(tick_lower)).call()
    upper=pool_c.functions.ticks(int(tick_upper)).call()
    lower_out0,lower_out1=int(lower[2]),int(lower[3])
    upper_out0,upper_out1=int(upper[2]),int(upper[3])

    if int(current_tick) >= int(tick_lower):
        below0,below1=lower_out0,lower_out1
    else:
        below0,below1=_u256_sub(global0,lower_out0),_u256_sub(global1,lower_out1)

    if int(current_tick) < int(tick_upper):
        above0,above1=upper_out0,upper_out1
    else:
        above0,above1=_u256_sub(global0,upper_out0),_u256_sub(global1,upper_out1)

    inside0=_u256_sub(_u256_sub(global0,below0),above0)
    inside1=_u256_sub(_u256_sub(global1,below1),above1)
    last0,last1=int(pos[8]),int(pos[9])
    owed0,owed1=int(pos[10]),int(pos[11])
    delta0=_u256_sub(inside0,last0)
    delta1=_u256_sub(inside1,last1)
    q128=1 << 128
    raw0=owed0 + (int(liquidity)*delta0 // q128)
    raw1=owed1 + (int(liquidity)*delta1 // q128)
    return raw0/(10**int(dec0)), raw1/(10**int(dec1)), "POOL_FEE_GROWTH"


def _rolling_fee_tracker(store, position_id: str, snapshot: dict[str,Any], opened_at: float, capital_value: float) -> dict[str,Any]:
    """Track fee accrual from raw owed-token changes rather than USD mark changes.

    First observation treats current unclaimed amounts as fees earned since mint.
    Later positive token deltas are new fee accrual; negative deltas are recorded as
    a collection lower bound and never erase previously-earned fees.
    """
    now=float(snapshot.get("read_at") or time.time())
    t0=snapshot.get("token0") or {}; t1=snapshot.get("token1") or {}
    current=[float(t0.get("unclaimed") or 0),float(t1.get("unclaimed") or 0)]
    prices=[float(t0.get("price_usd") or 0),float(t1.get("price_usd") or 0)]
    key=f"fees:tracker:{position_id}"
    tr=store.get_setting(key,{}) or {}
    cumulative=float(tr.get("cumulative_earned_usd") or 0); collected=float(tr.get("collected_lower_bound_usd") or 0)
    last=tr.get("last") or {}; observations=list(tr.get("observations") or [])
    if not last:
        initial=sum(current[i]*prices[i] for i in (0,1))
        cumulative=max(0.0,initial)
        start=float(opened_at or now)
        observations=[{"timestamp":start,"cumulative_earned_usd":0.0},{"timestamp":now,"cumulative_earned_usd":cumulative}]
    else:
        previous=[float(last.get("u0") or 0),float(last.get("u1") or 0)]
        earned=0.0; collected_now=0.0
        for i in (0,1):
            delta=current[i]-previous[i]
            if delta>=0: earned += delta*prices[i]
            else: collected_now += (-delta)*prices[i]
        cumulative += max(0.0,earned); collected += max(0.0,collected_now)
        obs={"timestamp":now,"cumulative_earned_usd":cumulative}
        if observations and now-float(observations[-1].get("timestamp") or 0)<300:
            observations[-1]=obs
        else:
            observations.append(obs)
    cutoff=now-35*86400
    observations=[x for x in observations if float(x.get("timestamp") or 0)>=cutoff or x is observations[0]]
    def rolling(seconds: float) -> float:
        target=now-seconds; baseline=0.0
        before=[x for x in observations if float(x.get("timestamp") or 0)<=target]
        if before: baseline=float(before[-1].get("cumulative_earned_usd") or 0)
        return max(0.0,cumulative-baseline)
    age_days=max((now-float(opened_at or now))/86400.0,1/24)
    pace_apr=(cumulative/max(float(capital_value or 0),1e-9))/age_days*365*100 if capital_value>0 else 0.0
    out={
        "first_seen_at":float(tr.get("first_seen_at") or now),"opened_at":float(opened_at or 0),
        "last":{"timestamp":now,"u0":current[0],"u1":current[1]},
        "cumulative_earned_usd":cumulative,"collected_lower_bound_usd":collected,"observations":observations,
        "fees_24h_usd":rolling(86400),"fees_7d_usd":rolling(7*86400),"fees_30d_usd":rolling(30*86400),
        "annualised_fee_pace_pct":pace_apr,"age_days":age_days,"quality":"TOKEN_AMOUNT_DELTA_TRACKER",
    }
    observed=observed_fee_metrics(out,float(capital_value or 0))
    out["display_annualised_fee_apr_pct"]=observed.get("since_open_annualised_fee_apr_pct")
    out["spot_1d_annualised_fee_apr_pct"]=observed.get("spot_1d_annualised_fee_apr_pct")
    out["annualisation_suppressed"]=observed.get("annualisation_suppressed")
    out["observation_confidence"]=observed.get("confidence")
    out["observation_warning"]=observed.get("warning")
    store.set_setting(key,out)
    snapshot["fee_tracking"]={k:v for k,v in out.items() if k not in {"observations","last"}}
    return out


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


def _historical_major_symbol_mark(market: Any, symbol: str, opened_at: float) -> float:
    """Nearest global USD mark for a major when new-chain address history is sparse."""
    if not market or not opened_at or str(symbol or "").upper() not in {"WETH","ETH","WBTC","BTC"}:
        return 0.0
    if not hasattr(market,"alchemy_symbol_history"):
        return 0.0
    try:
        rows=market.alchemy_symbol_history(str(symbol).upper(),3,timeframe="hour")
        usable=[x for x in rows or [] if float(x.get("timestamp") or 0)>0 and float(x.get("price_usd") or x.get("close") or 0)>0]
        if not usable:
            return 0.0
        nearest=min(usable,key=lambda x:abs(float(x.get("timestamp") or 0)-float(opened_at)))
        if abs(float(nearest.get("timestamp") or 0)-float(opened_at))>6*3600:
            return 0.0
        return float(nearest.get("price_usd") or nearest.get("close") or 0)
    except Exception:
        return 0.0


def _historical_pool_marks_at(
    market: GeckoTerminalClient | None, chain: str, pool_address: str, market_row: dict[str, Any],
    token0: str, token1: str, opened_at: float,
) -> tuple[float, float, dict[str, Any]]:
    """Nearest same-pool token/USD marks around the mint timestamp.

    GeckoTerminal can return OHLC in USD for either the pool base or quote token.
    That gives us a defensible opening USD mark for small tokens which may not have
    standalone historical price coverage, while keeping the exact deposited token
    amounts authoritative from the mint transaction receipt.
    """
    if not market or not opened_at or not pool_address or not market_row:
        return 0.0,0.0,{"source":"UNAVAILABLE"}
    age_days=max(2,min(30,int((time.time()-float(opened_at))/86400)+2))
    base_addr=str((market_row.get("base_token") or {}).get("address") or "").lower()
    quote_addr=str((market_row.get("quote_token") or {}).get("address") or "").lower()
    marks={}
    details={}
    for side in ("base","quote"):
        try:
            rows=market.ohlcv_days(chain,pool_address,age_days,timeframe="hour",token=side)
        except Exception as exc:
            details[f"{side}_error"]=str(exc)[:120]
            continue
        if not rows:
            continue
        nearest=min(rows,key=lambda r:abs(float(r.get("timestamp") or 0)-float(opened_at)))
        delta=abs(float(nearest.get("timestamp") or 0)-float(opened_at))
        if delta<=6*3600:
            marks[side]=max(0.0,float(nearest.get("close") or 0))
            details[f"{side}_timestamp"]=float(nearest.get("timestamp") or 0)
            details[f"{side}_distance_seconds"]=round(delta,1)
    a0=str(token0 or "").lower(); a1=str(token1 or "").lower()
    p0=p1=0.0
    if a0==base_addr: p0=marks.get("base",0.0)
    elif a0==quote_addr: p0=marks.get("quote",0.0)
    if a1==base_addr: p1=marks.get("base",0.0)
    elif a1==quote_addr: p1=marks.get("quote",0.0)
    details["source"]="GECKOTERMINAL_SAME_POOL_TOKEN_USD_AT_OPEN" if (p0>0 or p1>0) else "UNAVAILABLE"
    return p0,p1,details


def _position_lifecycle_events(
    w3: Web3, manager: str, token_id: int, from_block: int, to_block: int, *, chunk: int = 25_000
) -> list[dict[str, Any]]:
    """Read immutable manager lifecycle events for one V3 NFT."""
    topic_id="0x"+int(token_id).to_bytes(32,"big").hex()
    definitions={
        "INCREASE_LIQUIDITY":INCREASE_LIQUIDITY_TOPIC,
        "DECREASE_LIQUIDITY":DECREASE_LIQUIDITY_TOPIC,
        "COLLECT":COLLECT_TOPIC,
    }
    out=[]
    start=max(0,int(from_block or 0)); end_block=max(start,int(to_block or start))
    while start<=end_block:
        end=min(end_block,start+chunk-1)
        for event_type,topic0 in definitions.items():
            try:
                logs=w3.eth.get_logs({
                    "fromBlock":start,"toBlock":end,
                    "address":Web3.to_checksum_address(manager),
                    "topics":[topic0,topic_id],
                })
            except Exception:
                logs=[]
            for log in logs:
                raw=log.get("data")
                try:
                    if isinstance(raw,(bytes,bytearray)) or hasattr(raw,"hex"):
                        data=bytes(raw)
                    else:
                        data=bytes.fromhex(str(raw or "0x").removeprefix("0x"))
                    if event_type=="COLLECT":
                        if len(data)<96: continue
                        amount0=int.from_bytes(data[32:64],"big")
                        amount1=int.from_bytes(data[64:96],"big")
                        liquidity=0
                    else:
                        if len(data)<96: continue
                        liquidity=int.from_bytes(data[0:32],"big")
                        amount0=int.from_bytes(data[32:64],"big")
                        amount1=int.from_bytes(data[64:96],"big")
                    txh=log.get("transactionHash")
                    out.append({
                        "event_type":event_type,
                        "block_number":int(log.get("blockNumber") or 0),
                        "log_index":int(log.get("logIndex") or 0),
                        "transaction_hash":txh.hex() if hasattr(txh,"hex") else str(txh or ""),
                        "liquidity":liquidity,"amount0_raw":amount0,"amount1_raw":amount1,
                    })
                except Exception:
                    continue
        start=end+1
    out.sort(key=lambda x:(x["block_number"],x["log_index"]))
    return out


def _event_token_marks(
    *, w3: Web3, pool_c: Any, market: Any, cfg: ChainConfig, pool: str, market_row: dict[str, Any],
    token0: str, token1: str, sym0: str, sym1: str, dec0: int, dec1: int,
    block_number: int, timestamp: float,
) -> tuple[float,float,list[str]]:
    """Best historical USD marks at one lifecycle event."""
    p0=p1=0.0; sources=[]
    s0,s1=str(sym0).upper(),str(sym1).upper()
    if s0 in STABLES: p0=1.0; sources.append(f"{s0}_PEG")
    if s1 in STABLES: p1=1.0; sources.append(f"{s1}_PEG")
    if market and timestamp:
        if p0<=0:
            try:
                p0=float(market.historical_token_price_at(cfg.key,{"address":token0,"symbol":sym0},timestamp) or 0)
                if p0>0: sources.append(f"HISTORICAL_{s0}_USD")
            except Exception: pass
        if p1<=0:
            try:
                p1=float(market.historical_token_price_at(cfg.key,{"address":token1,"symbol":sym1},timestamp) or 0)
                if p1>0: sources.append(f"HISTORICAL_{s1}_USD")
            except Exception: pass
        if p0<=0 and s0 in MAJORS:
            p0=_historical_major_symbol_mark(market,s0,timestamp)
            if p0>0: sources.append(f"GLOBAL_{s0}_USD")
        if p1<=0 and s1 in MAJORS:
            p1=_historical_major_symbol_mark(market,s1,timestamp)
            if p1>0: sources.append(f"GLOBAL_{s1}_USD")
        if (p0<=0 or p1<=0) and market_row:
            try:
                hp0,hp1,_=_historical_pool_marks_at(market,cfg.key,pool,market_row,token0,token1,timestamp)
                if p0<=0 and hp0>0: p0=hp0; sources.append("POOL_TOKEN0_USD")
                if p1<=0 and hp1>0: p1=hp1; sources.append("POOL_TOKEN1_USD")
            except Exception: pass

    ratio=0.0
    if block_number:
        try:
            tick=int(pool_c.functions.slot0().call(block_identifier=int(block_number))[1])
            ratio=_raw_price_at_tick(tick,dec0,dec1)
            if ratio>0: sources.append("ARCHIVE_POOL_RATIO")
        except Exception:
            ratio=0.0
    if ratio>0:
        if p0>0 and p1<=0: p1=p0/ratio; sources.append("POOL_RATIO_DERIVED_TOKEN1")
        elif p1>0 and p0<=0: p0=ratio*p1; sources.append("POOL_RATIO_DERIVED_TOKEN0")
    return p0,p1,sources


def _closed_position_final(
    *, w3: Web3, cfg: ChainConfig, wallet: str, manager: Any, pool_c: Any, pool: str,
    token_id: int, opening_block: int, latest_block: int, market: Any, market_row: dict[str, Any],
    token0: str, token1: str, sym0: str, sym1: str, dec0: int, dec1: int,
    opening_entry: dict[str, Any], current_unclaimed_usd: float,
    current_owed0: float = 0.0, current_owed1: float = 0.0,
) -> dict[str, Any]:
    """Reconstruct one closed NFT once, then freeze the result.

    Cash P/L is based on actual manager events: all liquidity contributions are
    cost basis; all Collect distributions are proceeds; gas is a separate actual
    transaction cost. Fee income is each Collect minus outstanding principal
    created by earlier DecreaseLiquidity events, including when decrease/collect
    happen in different transactions.
    """
    if current_unclaimed_usd>0.005 or current_owed0>1e-12 or current_owed1>1e-12:
        return {"complete":False,"reason":"UNCLAIMED_TOKENS_REMAIN"}
    events=_position_lifecycle_events(
        w3,cfg.position_manager,token_id,max(0,int(opening_block or 0)),latest_block
    )
    if not events:
        return {"complete":False,"reason":"NO_LIFECYCLE_EVENTS"}
    block_times={}
    for ev in events:
        block=int(ev.get("block_number") or 0)
        if block not in block_times:
            try: block_times[block]=float(w3.eth.get_block(block).get("timestamp") or 0)
            except Exception: block_times[block]=0.0
        ev["timestamp"]=block_times[block]
        ev["amount0"]=float(ev.get("amount0_raw") or 0)/(10**int(dec0))
        ev["amount1"]=float(ev.get("amount1_raw") or 0)/(10**int(dec1))
        p0,p1,sources=_event_token_marks(
            w3=w3,pool_c=pool_c,market=market,cfg=cfg,pool=pool,market_row=market_row,
            token0=token0,token1=token1,sym0=sym0,sym1=sym1,dec0=dec0,dec1=dec1,
            block_number=block,timestamp=ev["timestamp"],
        )
        ev["token0_price_usd"]=p0; ev["token1_price_usd"]=p1
        ev["value_usd"]=ev["amount0"]*p0+ev["amount1"]*p1 if (p0>0 or ev["amount0"]<=0) and (p1>0 or ev["amount1"]<=0) else None
        ev["price_sources"]=sources

    contributions=distributions=fees=0.0
    valuations_complete=True
    fee_events=[]
    pending_principal0=pending_principal1=0.0
    opening_basis=float(opening_entry.get("entry_value_usd") or 0)
    opening_tx=str(opening_entry.get("transaction_hash") or "").lower()
    opening_counted=False
    has_collect=False

    for ev in events:
        et=str(ev.get("event_type") or "")
        txh=str(ev.get("transaction_hash") or "").lower()
        a0=float(ev.get("amount0") or 0); a1=float(ev.get("amount1") or 0)
        if et=="INCREASE_LIQUIDITY":
            if (not opening_counted) and opening_tx and txh==opening_tx and bool(opening_entry.get("basis_complete")) and opening_basis>0:
                contributions+=opening_basis
                opening_counted=True
            elif ev.get("value_usd") is None:
                valuations_complete=False
            else:
                contributions+=float(ev["value_usd"])
        elif et=="DECREASE_LIQUIDITY":
            pending_principal0+=a0
            pending_principal1+=a1
        elif et=="COLLECT":
            has_collect=True
            if ev.get("value_usd") is None:
                valuations_complete=False
            else:
                distributions+=float(ev["value_usd"])
            principal0=min(a0,pending_principal0)
            principal1=min(a1,pending_principal1)
            pending_principal0=max(0.0,pending_principal0-principal0)
            pending_principal1=max(0.0,pending_principal1-principal1)
            fee0=max(0.0,a0-principal0)
            fee1=max(0.0,a1-principal1)
            p0=float(ev.get("token0_price_usd") or 0); p1=float(ev.get("token1_price_usd") or 0)
            if (fee0<=0 or p0>0) and (fee1<=0 or p1>0):
                fee_value=fee0*p0+fee1*p1
                fees+=fee_value
                fee_events.append({"transaction_hash":txh,"timestamp":ev.get("timestamp"),"token0":fee0,"token1":fee1,"fee_value_usd":fee_value})
            elif fee0>0 or fee1>0:
                valuations_complete=False

    # Opening IncreaseLiquidity should normally be present. If event valuation was
    # unavailable but the opening reconstruction is verified, retain that basis.
    if not opening_counted and bool(opening_entry.get("basis_complete")) and opening_basis>0:
        contributions+=opening_basis
        opening_counted=True

    gas_native=gas_usd=0.0; gas_complete=True; gas_quality="HISTORICAL_NATIVE_PRICE"
    tx_hashes=sorted({str(x.get("transaction_hash") or "") for x in events if x.get("transaction_hash")})
    for txh in tx_hashes:
        try:
            receipt=w3.eth.get_transaction_receipt(txh)
            tx=w3.eth.get_transaction(txh)
            if str(tx.get("from") or "").lower()!=str(wallet).lower():
                continue
            used=int(receipt.get("gasUsed") or 0)
            price=int(receipt.get("effectiveGasPrice") or tx.get("gasPrice") or 0)
            native=used*price/1e18
            gas_native+=native
            block=int(receipt.get("blockNumber") or 0)
            ts=block_times.get(block,0.0)
            if not ts:
                try: ts=float(w3.eth.get_block(block).get("timestamp") or 0)
                except Exception: ts=0.0
            native_price=_historical_major_symbol_mark(market,"WETH",ts) if market and ts else 0.0
            if native>0 and native_price<=0 and market:
                try:
                    cfg_native=str(cfg.wrapped_native or "")
                    mark=(market.token_prices(cfg.key,[cfg_native]) or {}).get(cfg_native.lower()) if cfg_native else None
                    native_price=float(mark or 0)
                    if native_price>0:
                        gas_quality="CURRENT_NATIVE_PRICE_FALLBACK"
                except Exception:
                    native_price=0.0
            if native>0 and native_price<=0:
                gas_complete=False
            gas_usd+=native*native_price
        except Exception:
            gas_complete=False

    closed_at=max([float(x.get("timestamp") or 0) for x in events] or [0.0])
    principal_settled=pending_principal0<=1e-12 and pending_principal1<=1e-12
    complete=bool(contributions>0 and has_collect and valuations_complete and gas_complete and principal_settled)
    pnl=(distributions-contributions-gas_usd) if complete else None
    return {
        "complete":complete,
        "quality":(
            "ONCHAIN_LIFECYCLE_FINAL_ESTIMATED_GAS" if complete and gas_quality!="HISTORICAL_NATIVE_PRICE"
            else "ONCHAIN_LIFECYCLE_FINAL" if complete else "ONCHAIN_LIFECYCLE_PARTIAL"
        ),
        "token_id":str(token_id),"opened_at":float(opening_entry.get("opened_at") or 0),
        "closed_at":closed_at,"opening_capital_usd":round(contributions,4),
        "total_distributions_usd":round(distributions,4),"total_fees_usd":round(fees,4),
        "gas_native":round(gas_native,10),"gas_usd":round(gas_usd,4),"gas_quality":gas_quality,
        "realised_pnl_usd":round(pnl,4) if pnl is not None else None,
        "realised_return_pct":round(pnl/contributions*100.0,4) if pnl is not None and contributions>0 else None,
        "events":events,"fee_events":fee_events,
        "valuation_complete":valuations_complete,"gas_complete":gas_complete,
        "principal_settled":principal_settled,
        "reason":None if complete else "Historical token/gas valuation or settlement evidence is incomplete",
    }


def scan_chain_positions(cfg: ChainConfig, wallet: str, *, scan_blocks: int, market: GeckoTerminalClient | None = None, from_block_override: int | None = None, known_token_ids: set[int] | None = None, seed_token_ids: set[int] | None = None, seed_evidence: dict[int, dict[str, Any]] | None = None, finalized_token_ids: set[int] | None = None) -> ScanResult:
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
        if finalized_token_ids:
            token_ids.difference_update(set(finalized_token_ids))
        log_ids: set[int] = set()
        try:
            log_ids, log_evidence = _discover_incoming_token_ids(w3, cfg.position_manager, Web3.to_checksum_address(wallet), start, latest)
            token_ids.update(log_ids)
            if finalized_token_ids:
                token_ids.difference_update(set(finalized_token_ids))
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
                fee_source="UNKNOWN"
                try:
                    fee0,fee1,fee_source=_live_v3_fees_from_growth(
                        pool_c,pos,current_tick,tick_lower,tick_upper,liquidity,dec0,dec1
                    )
                except Exception:
                    try:
                        quoted = manager.functions.collect((token_id, Web3.to_checksum_address(wallet), UINT128_MAX, UINT128_MAX)).call({"from": Web3.to_checksum_address(wallet)})
                        fee0, fee1 = float(quoted[0]) / (10 ** dec0), float(quoted[1]) / (10 ** dec1)
                        fee_source="COLLECT_ETH_CALL"
                    except Exception:
                        # Stored tokensOwed can be stale; keep it only as a final
                        # conservative fallback and expose that quality in snapshot.
                        fee0, fee1 = float(pos[10]) / (10 ** dec0), float(pos[11]) / (10 ** dec1)
                        fee_source="STORED_TOKENS_OWED_FALLBACK"
                market_row={}
                usd0=usd1=0.0
                if market:
                    try:
                        # token_prices prefers configured Alchemy, preserving the
                        # scarce GeckoTerminal budget for data that cannot be read
                        # from chain/Alchemy.
                        marks=market.token_prices(cfg.key,[token0,token1])
                        usd0=float(marks.get(str(token0).lower()) or 0)
                        usd1=float(marks.get(str(token1).lower()) or 0)
                    except Exception:
                        pass
                if market and (usd0<=0 or usd1<=0):
                    market_row=_pool_market(market,cfg.key,pool)
                    mp0,mp1=_usd_prices(market_row,token0,token1)
                    usd0=usd0 or mp0
                    usd1=usd1 or mp1
                value_usd = amount0 * usd0 + amount1 * usd1
                fees_usd = fee0 * usd0 + fee1 * usd1
                discovered = discovery_evidence.get(token_id) or {}
                if not discovered.get("transaction_hash"):
                    recovered=_discover_opening_evidence_blockscout(cfg,Web3.to_checksum_address(wallet),token_id)
                    if recovered:
                        discovered={**{k:v for k,v in discovered.items() if v},**recovered}
                        discovery_evidence[token_id]=discovered
                opened_at = float(discovered.get("opened_at") or 0)
                if not opened_at and discovered.get("block_number"):
                    try: opened_at=float(w3.eth.get_block(int(discovered["block_number"])).get("timestamp") or 0)
                    except Exception: pass
                entry_evidence={}
                opening_tx=str(discovered.get("transaction_hash") or "")
                opening_block=int(discovered.get("block_number") or 0)
                if opening_tx:
                    try:
                        receipt=w3.eth.get_transaction_receipt(opening_tx)
                        opening_block=opening_block or int(receipt.get("blockNumber") or 0)
                        if opened_at <= 0 and opening_block:
                            try:
                                opened_at=float(w3.eth.get_block(opening_block).get("timestamp") or 0)
                            except Exception:
                                pass
                        dep0,dep1=_erc20_deposits_from_receipt(receipt,pool=pool,token0=token0,token1=token1,dec0=dec0,dec1=dec1)
                        mint_event=_opening_liquidity_event(
                            receipt,manager=cfg.position_manager,token_id=token_id,
                            tick_lower=tick_lower,tick_upper=tick_upper,dec0=dec0,dec1=dec1,
                        )
                        if mint_event:
                            # IncreaseLiquidity reports the amounts actually accepted
                            # by the NFT manager, which is stronger evidence than
                            # summing all token transfers in a complex transaction.
                            dep0=float(mint_event.get("amount0") or dep0)
                            dep1=float(mint_event.get("amount1") or dep1)
                        entry_tick=None
                        if opening_block:
                            try: entry_tick=int(pool_c.functions.slot0().call(block_identifier=opening_block)[1])
                            except Exception: entry_tick=None
                        if entry_tick is None and mint_event.get("reconstructed_tick") is not None:
                            entry_tick=int(mint_event["reconstructed_tick"])
                        entry_ratio=_raw_price_at_tick(entry_tick,dec0,dec1) if entry_tick is not None else 0.0
                        e0=e1=0.0
                        sources=[]
                        s0,s1=sym0.upper(),sym1.upper()
                        if s0 in STABLES:
                            e0=1.0; sources.append(f"{s0}_PEG")
                        if s1 in STABLES:
                            e1=1.0; sources.append(f"{s1}_PEG")

                        # Prefer direct historical USD marks for each non-stable token.
                        # This avoids requiring an archive-capable RPC merely to value
                        # the mint when Alchemy price history is available.
                        if market and opened_at:
                            if dep0>0 and e0<=0:
                                try:
                                    e0=float(market.historical_token_price_at(
                                        cfg.key,{"address":token0,"symbol":sym0},opened_at
                                    ) or 0)
                                    if e0>0: sources.append(f"HISTORICAL_{s0}_USD")
                                except Exception:
                                    pass
                            if dep1>0 and e1<=0:
                                try:
                                    e1=float(market.historical_token_price_at(
                                        cfg.key,{"address":token1,"symbol":sym1},opened_at
                                    ) or 0)
                                    if e1>0: sources.append(f"HISTORICAL_{s1}_USD")
                                except Exception:
                                    pass
                            if dep0>0 and e0<=0 and s0 in {"WETH","ETH","WBTC","BTC"}:
                                e0=_historical_major_symbol_mark(market,s0,opened_at)
                                if e0>0: sources.append(f"GLOBAL_{s0}_USD_AT_OPEN")
                            if dep1>0 and e1<=0 and s1 in {"WETH","ETH","WBTC","BTC"}:
                                e1=_historical_major_symbol_mark(market,s1,opened_at)
                                if e1>0: sources.append(f"GLOBAL_{s1}_USD_AT_OPEN")

                        # Small/new tokens often have no standalone historical
                        # price feed. Ask the exact pool for base/quote token USD OHLC
                        # around the mint time before giving up on opening valuation.
                        pool_mark_detail={}
                        if market and opened_at and ((dep0>0 and e0<=0) or (dep1>0 and e1<=0)):
                            try:
                                if not market_row:
                                    market_row=_pool_market(market,cfg.key,pool)
                                hp0,hp1,pool_mark_detail=_historical_pool_marks_at(
                                    market,cfg.key,pool,market_row,token0,token1,opened_at
                                )
                                if dep0>0 and e0<=0 and hp0>0:
                                    e0=hp0; sources.append("POOL_HISTORICAL_TOKEN0_USD")
                                if dep1>0 and e1<=0 and hp1>0:
                                    e1=hp1; sources.append("POOL_HISTORICAL_TOKEN1_USD")
                            except Exception:
                                pool_mark_detail={}

                        # If only one side has a USD mark, an opening-block pool ratio
                        # can price the other side. If the RPC is not archive-capable,
                        # we leave the basis incomplete rather than invent P/L.
                        if entry_ratio>0:
                            if e0>0 and e1<=0:
                                e1=e0/entry_ratio; sources.append("OPENING_POOL_RATIO_DERIVED_TOKEN1")
                            elif e1>0 and e0<=0:
                                e0=entry_ratio*e1; sources.append("OPENING_POOL_RATIO_DERIVED_TOKEN0")

                        has_deposit=(dep0>0 or dep1>0)
                        basis_complete=bool(
                            has_deposit
                            and (dep0<=0 or e0>0)
                            and (dep1<=0 or e1>0)
                        )
                        partial_entry_value=dep0*e0+dep1*e1 if (e0>0 or e1>0) else 0.0
                        entry_value=partial_entry_value if basis_complete else 0.0
                        missing=[]
                        if dep0>0 and e0<=0: missing.append(sym0)
                        if dep1>0 and e1<=0: missing.append(sym1)
                        entry_evidence={
                            "transaction_hash":opening_tx,"block_number":opening_block,"opened_at":opened_at,
                            "token0_amount":dep0,"token1_amount":dep1,"token0_price_usd":e0,"token1_price_usd":e1,
                            "entry_value_usd":entry_value,"partial_entry_value_usd":partial_entry_value,
                            "entry_tick":entry_tick,"raw_token1_per_token0":entry_ratio,
                            "price_source":"+".join(sources) if sources else "UNAVAILABLE",
                            "basis_complete":basis_complete,
                            "missing_opening_price_symbols":missing,
                            "historical_pool_mark_detail":pool_mark_detail,
                            "opening_liquidity_event":mint_event,
                            "quality":"ONCHAIN_MINT_RECONSTRUCTED" if basis_complete else "ONCHAIN_AMOUNTS_PARTIAL_PRICING",
                        }
                    except Exception as exc:
                        entry_evidence={"transaction_hash":opening_tx,"block_number":opening_block,"opened_at":opened_at,"quality":"OPENING_TX_FOUND_RECONSTRUCTION_FAILED","error":str(exc)[:180]}
                snapshot = {
                    "live": True, "chain": cfg.key, "chain_id": cfg.chain_id, "block_number": latest, "read_at": time.time(),
                    "discovery_source": discovered.get("discovery_source") or ("BLOCKSCOUT_OWNED_NFT" if token_id in explorer_ids else "ALCHEMY_OWNED_NFT" if token_id in alchemy_ids else "KNOWN_POSITION"),
                    "opening_transaction_hash": discovered.get("transaction_hash") or "",
                    "opening_block_number": int(opening_block or discovered.get("block_number") or 0),
                    "opened_at": opened_at,
                    "wallet": Web3.to_checksum_address(wallet), "token_id": str(token_id), "position_manager": cfg.position_manager,
                    "factory": cfg.factory, "pool_address": pool, "fee_tier": fee, "tick_lower": tick_lower, "tick_upper": tick_upper,
                    "current_tick": current_tick, "liquidity": str(liquidity), "token0": {"address": token0, "symbol": sym0, "decimals": dec0, "amount": amount0, "unclaimed": fee0, "price_usd": usd0},
                    "token1": {"address": token1, "symbol": sym1, "decimals": dec1, "amount": amount1, "unclaimed": fee1, "price_usd": usd1},
                    "display_inverted": inverted, "price_lens": lens, "range_unit": lens["unit"], "range_unit_label": lens["unit_label"],
                    "current_value_usd": value_usd, "unclaimed_fees_usd": fees_usd, "market": market_row,
                    "entry_evidence": entry_evidence,
                    "fee_source":fee_source,
                    "fee_read_at":time.time(),
                    "data_quality": "LIVE_CHAIN_PLUS_MARKET" if (usd0>0 and usd1>0) else "LIVE_CHAIN_PARTIAL_USD_MARKET",
                }
                if liquidity<=0 and fees_usd<=0.005:
                    try:
                        closed_final=_closed_position_final(
                            w3=w3,cfg=cfg,wallet=wallet,manager=manager,pool_c=pool_c,pool=pool,
                            token_id=token_id,opening_block=int(opening_block or discovered.get("block_number") or 0),
                            latest_block=latest,market=market,market_row=market_row,
                            token0=token0,token1=token1,sym0=sym0,sym1=sym1,dec0=dec0,dec1=dec1,
                            opening_entry=entry_evidence,current_unclaimed_usd=fees_usd,
                            current_owed0=fee0,current_owed1=fee1,
                        )
                        snapshot["closed_final"]=closed_final
                    except Exception as exc:
                        snapshot["closed_final"]={"complete":False,"quality":"ONCHAIN_LIFECYCLE_PARTIAL","reason":str(exc)[:180]}
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
    # Backward-compatible helper for tests/older call sites. New reconciliation
    # uses token-id backed labels so rescans never renumber existing live NFTs.
    return _live_display_name(store, f"legacy-{int(time.time()*1000)}", pair)


def finalise_closed_positions_from_store(
    store, cfg: ChainConfig, wallet: str, market: GeckoTerminalClient | None = None, *, limit: int = 20
) -> dict[str, Any]:
    """Attempt one-time lifecycle reconstruction for closed live NFTs.

    This deliberately uses the last persisted live snapshot because a closed/burned
    NFT may no longer be owned and therefore cannot be rebuilt from ownerOf/positions
    on the normal owned-position scan path.
    """
    if not cfg.rpc_url() or not wallet:
        return {"attempted":0,"finalised":0,"partial":0,"errors":[]}
    rows=[
        p for p in store.list_positions("CLOSED")
        if str(p.get("chain") or "").upper()==cfg.key
        and str(p.get("source") or "")=="live_chain"
        and str(p.get("lifecycle_stage") or "").upper()!="CLOSED_FINAL"
        and str(p.get("token_id") or "").isdigit()
    ][:max(1,int(limit))]
    if not rows:
        return {"attempted":0,"finalised":0,"partial":0,"errors":[]}
    try:
        w3=build_read_only_web3(cfg.rpc_url())
        latest=int(w3.eth.block_number)
    except Exception as exc:
        return {"attempted":0,"finalised":0,"partial":len(rows),"errors":[str(exc)]}

    attempted=finalised=partial=0; errors=[]
    for p in rows:
        attempted+=1
        pid=str(p.get("id") or "")
        snap=store.get_position_snapshot(pid) or {}
        try:
            t0=dict(snap.get("token0") or {}); t1=dict(snap.get("token1") or {})
            pool=str(snap.get("pool_address") or p.get("pool_address") or "")
            token0=str(t0.get("address") or ""); token1=str(t1.get("address") or "")
            if not (pool and token0 and token1):
                raise ValueError("Persisted token/pool metadata is incomplete")
            dec0=int(t0.get("decimals") or 18); dec1=int(t1.get("decimals") or 18)
            sym0=str(t0.get("symbol") or "TOKEN0"); sym1=str(t1.get("symbol") or "TOKEN1")
            opening=dict(snap.get("entry_evidence") or {})
            opening_block=int(snap.get("opening_block_number") or opening.get("block_number") or 0)
            pool_c=w3.eth.contract(address=Web3.to_checksum_address(pool),abi=POOL_ABI)
            market_row={}
            final=_closed_position_final(
                w3=w3,cfg=cfg,wallet=wallet,manager=None,pool_c=pool_c,pool=pool,
                token_id=int(p.get("token_id")),opening_block=opening_block,latest_block=latest,
                market=market,market_row=market_row,
                token0=token0,token1=token1,sym0=sym0,sym1=sym1,dec0=dec0,dec1=dec1,
                opening_entry=opening,current_unclaimed_usd=0.0,current_owed0=0.0,current_owed1=0.0,
            )
            snap["closed_final"]=final
            snap["closed_final_checked_at"]=time.time()
            store.save_position_snapshot(pid,snap)
            if final.get("complete"):
                quality=str(final.get("quality") or "ONCHAIN_LIFECYCLE_FINAL")
                store.finalize_closed_position(
                    pid,
                    opening_capital_usd=float(final.get("opening_capital_usd") or 0),
                    total_fees_usd=float(final.get("total_fees_usd") or 0),
                    gas_costs_usd=float(final.get("gas_usd") or 0),
                    realised_pnl_usd=float(final.get("realised_pnl_usd") or 0),
                    realised_return_pct=float(final.get("realised_return_pct") or 0),
                    closed_at=float(final.get("closed_at") or time.time()),
                    quality=quality,
                )
                finalised+=1
            else:
                partial+=1
        except Exception as exc:
            partial+=1
            errors.append({"position_id":pid,"error":str(exc)[:220]})
            snap["closed_final"]={
                **dict(snap.get("closed_final") or {}),
                "complete":False,"quality":"ONCHAIN_LIFECYCLE_PARTIAL",
                "reason":str(exc)[:220],"checked_at":time.time(),
            }
            store.save_position_snapshot(pid,snap)
    return {"attempted":attempted,"finalised":finalised,"partial":partial,"errors":errors}


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
        snap=dict(row.get("snapshot") or {})
        previous_snap=store.get_position_snapshot(position_id) or {}
        entry=dict(snap.get("entry_evidence") or {})
        previous_entry=dict(previous_snap.get("entry_evidence") or {})

        # A transient provider/archive-history failure must never erase a previously
        # complete opening reconstruction. Conversely, an old partial reconstruction
        # must not be allowed to masquerade as a valid cost basis.
        if not _entry_basis_complete(entry) and _entry_basis_complete(previous_entry):
            entry=previous_entry
            snap["entry_evidence"]=previous_entry
            for key in ("opening_transaction_hash","opening_block_number","opened_at"):
                if previous_snap.get(key):
                    snap[key]=previous_snap.get(key)

        reconstructed_capital=float(entry.get("entry_value_usd") or 0) if _entry_basis_complete(entry) else 0.0
        existing_quality=str((existing or {}).get("cost_basis_quality") or "UNKNOWN").upper()
        existing_capital=float((existing or {}).get("capital_value") or 0)

        if reconstructed_capital>0:
            # Complete on-chain opening evidence is authoritative and replaces any
            # old first-observed or accidentally partial basis.
            capital_value=reconstructed_capital
            cost_quality="ONCHAIN_MINT_RECONSTRUCTED"
        else:
            # If V0.8.7 previously called a partial opening reconstruction strong,
            # explicitly demote it. Keep a neutral first-observed denominator for
            # fee pacing, but do not use it for P/L.
            prior_entry_complete=_entry_basis_complete(previous_entry)
            if existing_quality=="ONCHAIN_MINT_RECONSTRUCTED" and not prior_entry_complete:
                capital_value=current_value or existing_capital
                cost_quality="FIRST_OBSERVED"
            else:
                capital_value=existing_capital or current_value
                cost_quality=existing_quality if existing_quality not in {"","UNKNOWN"} else ("FIRST_OBSERVED" if current_value>0 else "UNKNOWN")

        evidence_opened=float(entry.get("opened_at") or snap.get("opened_at") or row.get("opened_at") or 0)
        existing_opened=float((existing or {}).get("opened_at") or 0)
        # When the opening transaction gives us a timestamp it wins outright.
        # "Earliest seen" timestamps from previous releases are not stronger evidence.
        if evidence_opened>0 and (entry.get("transaction_hash") or snap.get("opening_transaction_hash")):
            opened_at=evidence_opened
        else:
            opened_at=evidence_opened or existing_opened or time.time()

        old_notes = str((existing or {}).get("notes") or "")
        note = old_notes or "Live-chain position; entry economics are reconstructed from the opening NFT transaction when available."
        auto_sleeve=_auto_sleeve(str(row.get("pair") or ""))
        manual_metadata=bool(str((existing or {}).get("entry_thesis") or "").strip() or str((existing or {}).get("campaign_label") or "").strip())
        existing_sleeve=str((existing or {}).get("strategy_sleeve") or "").upper()
        sleeve=existing_sleeve if manual_metadata and existing_sleeve else auto_sleeve
        if not sleeve: sleeve=auto_sleeve
        current_name=str((existing or {}).get("display_name") or "")
        authoritative=authoritative_label(result.chain,str(row["token_id"]))
        if authoritative or (not current_name) or re.match(r"^P\d+\s*·",current_name,re.I):
            display_name=_live_display_name(
                store,str(row["token_id"]),str(row.get("pair") or "LP position"),result.chain
            )
        else:
            display_name=current_name
        tracker=_rolling_fee_tracker(store,position_id,snap,opened_at,capital_value)
        realised=max(float((existing or {}).get("realised_fees") or 0),float(tracker.get("collected_lower_bound_usd") or 0))
        closed_final=dict(snap.get("closed_final") or {})
        final_complete=bool(closed_final.get("complete"))
        if final_complete:
            realised=max(realised,float(closed_final.get("total_fees_usd") or 0))
            capital_value=float(closed_final.get("opening_capital_usd") or capital_value)
            cost_quality="ONCHAIN_MINT_RECONSTRUCTED"
        p = Position(
            id=position_id, protocol="UNISWAP_V3", chain=result.chain, pair=str(row["pair"]), status="OPEN" if row.get("active_liquidity") else "CLOSED",
            lower_price=float(row["lower_price"]), upper_price=float(row["upper_price"]), current_price=float(row["current_price"]),
            capital_value=capital_value, current_value=current_value, unclaimed_fees=float(row.get("unclaimed_fees") or 0),
            fees_today=float(tracker.get("fees_24h_usd") or 0), fees_7d=float(tracker.get("fees_7d_usd") or 0), fees_30d=float(tracker.get("fees_30d_usd") or 0),
            realised_fees=realised, estimated_il=float((existing or {}).get("estimated_il") or 0), gas_costs=float((existing or {}).get("gas_costs") or 0),
            apr_current=float(tracker.get("display_annualised_fee_apr_pct") or 0), apr_7d=float(tracker.get("display_annualised_fee_apr_pct") or 0), opened_at=opened_at,
            token_id=str(row["token_id"]), campaign_id=(existing or {}).get("campaign_id"), source="live_chain", notes=note,
            strategy_sleeve=sleeve, directional_bias=(existing or {}).get("directional_bias") or "NEUTRAL", inventory_intent=(existing or {}).get("inventory_intent") or "BALANCED",
            target_hold_days=(30.0 if sleeve == "CORE_INCOME" and not manual_metadata else float((existing or {}).get("target_hold_days") or (30 if sleeve == "CORE_INCOME" else 3))), monitoring_class=("LOW_TOUCH" if sleeve == "CORE_INCOME" and not manual_metadata else ((existing or {}).get("monitoring_class") or "ACTIVE")),
            display_name=display_name,
            campaign_label=(existing or {}).get("campaign_label") or "",
            entry_thesis=(existing or {}).get("entry_thesis") or "",
            exit_goal=(existing or {}).get("exit_goal") or "",
            lifecycle_stage="ACTIVE" if row.get("active_liquidity") else ("CLOSED_FINAL" if final_complete else "CLOSED"),
            cost_basis_quality=cost_quality,
            strategy_version="v0.8.11",
            pool_address=str((row.get("snapshot") or {}).get("pool_address") or (existing or {}).get("pool_address") or ""),
            range_unit=str((row.get("snapshot") or {}).get("range_unit") or (existing or {}).get("range_unit") or "TOKEN1_PER_TOKEN0"),
            closed_at=(float(closed_final.get("closed_at") or 0) if final_complete else (float((existing or {}).get("closed_at") or 0) if not row.get("active_liquidity") else 0)),
            reported_net_pnl=(float(closed_final.get("realised_pnl_usd") or 0) if final_complete else float((existing or {}).get("reported_net_pnl") or 0)),
            reported_net_pnl_pct=(float(closed_final.get("realised_return_pct") or 0) if final_complete else float((existing or {}).get("reported_net_pnl_pct") or 0)),
            pnl_quality=("ONCHAIN_LIFECYCLE_FINAL" if final_complete else str((existing or {}).get("pnl_quality") or "UNKNOWN")),
        )
        store.upsert_position(p)
        store.save_position_snapshot(position_id, snap)
        opening_hash=str(entry.get("transaction_hash") or snap.get("opening_transaction_hash") or "")
        if opening_hash and hasattr(store,"reconcile_execution_opening"):
            try:
                store.reconcile_execution_opening(opening_hash,position_id)
            except Exception:
                pass
        imported += 1
    # Never close an LP merely because one discovery source omitted it. Ownership
    # loss is authoritative only when ownerOf explicitly returns another owner.
    return {"chain": result.chain, "ok": result.ok, "positions": imported, "errors": errors, "discovered_token_ids": result.discovered_token_ids, "owned_nft_api_ids": result.owned_nft_api_ids, "alchemy_nft_api_ids": result.alchemy_nft_api_ids, "transfer_log_ids": result.transfer_log_ids, "discovery_sources": result.discovery_sources or [], "from_block": result.scanned_from_block, "latest_block": result.latest_block, "error": result.error}
