from __future__ import annotations

from typing import Any
import time

from .chain_registry import chain_config
from .rpc_client import build_read_only_web3
from .price_units import display_lens, tick_token1_per_token0

try:
    from web3 import Web3
except Exception:  # pragma: no cover - dependency-light test environments.
    Web3 = None  # type: ignore[assignment]

POOL_ABI=[
 {"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"fee","outputs":[{"name":"","type":"uint24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"tickSpacing","outputs":[{"name":"","type":"int24"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"liquidity","outputs":[{"name":"","type":"uint128"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"slot0","outputs":[{"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},{"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}],"stateMutability":"view","type":"function"},
 {"inputs":[{"name":"secondsAgos","type":"uint32[]"}],"name":"observe","outputs":[{"name":"tickCumulatives","type":"int56[]"},{"name":"secondsPerLiquidityCumulativeX128s","type":"uint160[]"}],"stateMutability":"view","type":"function"},
]
TOKEN_ABI=[
 {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
]
FACTORY_ABI=[
 {"inputs":[{"name":"tokenA","type":"address"},{"name":"tokenB","type":"address"},{"name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"name":"pool","type":"address"}],"stateMutability":"view","type":"function"},
]


def _require_web3():
    if Web3 is None:
        raise RuntimeError("Web3 is not installed; run pip install -r requirements.txt")
    return Web3


def _token_meta(w3,address: str)->tuple[str,int]:
    W3=_require_web3()
    c=w3.eth.contract(address=W3.to_checksum_address(address),abi=TOKEN_ABI)
    try: sym=str(c.functions.symbol().call())
    except Exception: sym=W3.to_checksum_address(address)[:8]
    try: dec=int(c.functions.decimals().call())
    except Exception: dec=18
    return sym,dec


def discover_v3_pair_fee_tiers(
    chain: str, token_a: str, token_b: str, fee_tiers: tuple[int,...]=(100,500,3000,10000)
) -> list[dict[str,Any]]:
    """Discover canonical Uniswap V3 pair pools directly from the factory.

    This avoids relying on a market-data provider's first page when comparing
    0.01%, 0.05%, 0.3% and 1% fee tiers.
    """
    cfg=chain_config(chain); url=cfg.rpc_url()
    if not url or not cfg.factory:
        return []
    try:
        W3=_require_web3()
        w3=build_read_only_web3(url)
        factory=w3.eth.contract(address=W3.to_checksum_address(cfg.factory),abi=FACTORY_ABI)
        a=W3.to_checksum_address(token_a); b=W3.to_checksum_address(token_b)
        zero="0x0000000000000000000000000000000000000000"
        out=[]
        for fee in fee_tiers:
            try:
                address=str(factory.functions.getPool(a,b,int(fee)).call())
            except Exception:
                continue
            if not address or address.lower()==zero:
                continue
            out.append({"pool_address":address,"fee_tier":int(fee),"fee_tier_bps":int(fee)/100.0,"source":"UNISWAP_V3_FACTORY"})
        return out
    except Exception:
        return []


def read_v3_pool_metadata(chain: str, address: str) -> dict[str,Any]:
    cfg=chain_config(chain); url=cfg.rpc_url()
    if not url: return {"ok":False,"chain":cfg.key,"error":"RPC unavailable"}
    try:
        W3=_require_web3()
        w3=build_read_only_web3(url); pool=w3.eth.contract(address=W3.to_checksum_address(address),abi=POOL_ABI)
        t0=pool.functions.token0().call(); t1=pool.functions.token1().call(); fee=int(pool.functions.fee().call()); spacing=int(pool.functions.tickSpacing().call()); active_liquidity=int(pool.functions.liquidity().call()); slot0=pool.functions.slot0().call(); tick=int(slot0[1])
        s0,d0=_token_meta(w3,t0); s1,d1=_token_meta(w3,t1)
        raw=tick_token1_per_token0(tick,d0,d1); lens=display_lens(s0,s1,raw,raw,raw)
        return {"ok":True,"chain":cfg.key,"pool_address":W3.to_checksum_address(address),"token0":{"address":t0,"symbol":s0,"decimals":d0},"token1":{"address":t1,"symbol":s1,"decimals":d1},"fee_tier":fee,"fee_tier_bps":fee/100.0,"fee_rate":fee/1_000_000.0,"tick_spacing":spacing,"current_tick":tick,"sqrt_price_x96":str(slot0[0]),"active_liquidity":str(active_liquidity),"liquidity":str(active_liquidity),"price_lens":lens}
    except Exception as exc:
        return {"ok":False,"chain":cfg.key,"pool_address":address,"error":str(exc)[:240]}



def read_v3_observation_history(
    chain: str, address: str, days: int, *, timeframe: str = "hour", max_points: int = 241
) -> list[dict[str, Any]]:
    """Read a pool-native historical price path from Uniswap V3 observations.

    This is the independent fallback used when public market-data APIs are
    throttled. It does not provide volume, so it is suitable for range geometry
    and volatility only. Fee economics still require separately evidenced volume
    or owned-position fee evidence.

    The pool may not retain the full requested lookback. We progressively shorten
    the window rather than failing the whole Profit Lab request.
    """
    cfg=chain_config(chain); url=cfg.rpc_url()
    if not url:
        return []
    try:
        W3=_require_web3()
        w3=build_read_only_web3(url)
        pool=w3.eth.contract(address=W3.to_checksum_address(address),abi=POOL_ABI)
        token0=pool.functions.token0().call()
        token1=pool.functions.token1().call()
        sym0,dec0=_token_meta(w3,token0)
        sym1,dec1=_token_meta(w3,token1)
    except Exception:
        return []

    step=3600 if str(timeframe).lower()=="hour" else 86400
    desired=max(step*12,min(int(max(1,days)*86400),step*max(12,int(max_points)-1)))
    now=int(time.time())
    duration=desired

    while duration>=step*12:
        # Keep the RPC call bounded while preserving the actual timestamps so the
        # range engine can infer samples/day correctly.
        intervals=max(12,min(int(max_points)-1,int(duration//step)))
        spacing=max(step,int(duration//intervals//step)*step)
        seconds=list(range(int(duration),-1,-int(spacing)))
        if not seconds or seconds[-1]!=0:
            seconds.append(0)
        seconds=sorted(set(max(0,min(2**32-1,int(x))) for x in seconds),reverse=True)
        if len(seconds)<13:
            duration//=2
            continue
        try:
            tick_cumulatives,_=pool.functions.observe(seconds).call()
        except Exception:
            duration//=2
            continue
        out=[]
        for i in range(len(seconds)-1):
            older=int(seconds[i]); newer=int(seconds[i+1]); elapsed=older-newer
            if elapsed<=0:
                continue
            try:
                avg_tick=float(int(tick_cumulatives[i+1])-int(tick_cumulatives[i]))/float(elapsed)
                raw=tick_token1_per_token0(avg_tick,dec0,dec1)
                lens=display_lens(sym0,sym1,raw,raw,raw)
                price=float(lens.get("current") or 0)
            except Exception:
                continue
            if price<=0:
                continue
            ts=now-newer
            out.append({
                "timestamp":ts,"open":price,"high":price,"low":price,"close":price,
                "volume":0.0,"source":"UNISWAP_V3_OBSERVE",
            })
        out.sort(key=lambda x:x["timestamp"])
        if len(out)>=12:
            return out
        duration//=2
    return []
