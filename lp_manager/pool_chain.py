from __future__ import annotations

from typing import Any

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
]
TOKEN_ABI=[
 {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
 {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
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
