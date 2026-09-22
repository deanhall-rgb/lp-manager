from __future__ import annotations

import time
from typing import Any

from web3 import Web3

from .chain_registry import chain_config
from .rpc_client import build_read_only_web3

UINT128_MAX = 2**128 - 1
DECREASE_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"liquidity","type":"uint128"},{"name":"amount0Min","type":"uint256"},{"name":"amount1Min","type":"uint256"},{"name":"deadline","type":"uint256"}],"name":"params","type":"tuple"}],"name":"decreaseLiquidity","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]
COLLECT_ABI = [{"inputs":[{"components":[{"name":"tokenId","type":"uint256"},{"name":"recipient","type":"address"},{"name":"amount0Max","type":"uint128"},{"name":"amount1Max","type":"uint128"}],"name":"params","type":"tuple"}],"name":"collect","outputs":[{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]
MULTICALL_ABI = [{"inputs":[{"name":"data","type":"bytes[]"}],"name":"multicall","outputs":[{"name":"results","type":"bytes[]"}],"stateMutability":"payable","type":"function"}]


def _hex(data: Any) -> str:
    return data.hex() if hasattr(data, "hex") else str(data)


def _encode(contract, fn: str, args: list[Any]) -> str:
    # web3 v7 exposes encode_abi; older v6 uses encodeABI.
    if hasattr(contract, "encode_abi"):
        return contract.encode_abi(fn, args=args)
    return contract.encodeABI(fn_name=fn, args=args)


def build_collect(snapshot: dict[str, Any]) -> dict[str, Any]:
    chain = str(snapshot.get("chain") or "")
    cfg = chain_config(chain)
    wallet = str(snapshot.get("wallet") or "")
    token_id = int(snapshot["token_id"])
    if not Web3.is_address(wallet):
        raise ValueError("Live snapshot has no valid wallet")
    w3 = build_read_only_web3(cfg.rpc_url())
    manager = w3.eth.contract(address=Web3.to_checksum_address(cfg.position_manager), abi=COLLECT_ABI)
    data = _encode(manager, "collect", [(token_id, Web3.to_checksum_address(wallet), UINT128_MAX, UINT128_MAX)])
    tx = {"chainId": cfg.chain_id, "to": cfg.position_manager, "from": Web3.to_checksum_address(wallet), "data": data, "value": "0x0"}
    simulation = {"ok": False, "method": "eth_call"}
    try:
        raw = w3.eth.call({"to": Web3.to_checksum_address(cfg.position_manager), "from": Web3.to_checksum_address(wallet), "data": data})
        simulation = {"ok": True, "method": "eth_call", "return_data": _hex(raw)}
    except Exception as exc:
        simulation = {"ok": False, "method": "eth_call", "error": str(exc)}
    return {"call": tx, "simulation": simulation, "manager": cfg.position_manager, "token_id": str(token_id)}


def build_close(snapshot: dict[str, Any], *, slippage_bps: int = 100, ttl_seconds: int = 1200) -> dict[str, Any]:
    chain = str(snapshot.get("chain") or "")
    cfg = chain_config(chain)
    wallet = str(snapshot.get("wallet") or "")
    token_id = int(snapshot["token_id"])
    liquidity = int(snapshot.get("liquidity") or 0)
    if liquidity <= 0:
        raise ValueError("Position has no active liquidity")
    if not Web3.is_address(wallet):
        raise ValueError("Live snapshot has no valid wallet")
    t0, t1 = snapshot.get("token0") or {}, snapshot.get("token1") or {}
    d0, d1 = int(t0.get("decimals") or 18), int(t1.get("decimals") or 18)
    expected0 = int(float(t0.get("amount") or 0) * (10 ** d0))
    expected1 = int(float(t1.get("amount") or 0) * (10 ** d1))
    slip = max(0, min(5000, int(slippage_bps)))
    min0 = expected0 * (10000-slip) // 10000
    min1 = expected1 * (10000-slip) // 10000
    deadline = int(time.time()) + max(60, int(ttl_seconds))
    w3 = build_read_only_web3(cfg.rpc_url())
    manager = w3.eth.contract(address=Web3.to_checksum_address(cfg.position_manager), abi=DECREASE_ABI + COLLECT_ABI + MULTICALL_ABI)
    dec = _encode(manager, "decreaseLiquidity", [(token_id, liquidity, min0, min1, deadline)])
    col = _encode(manager, "collect", [(token_id, Web3.to_checksum_address(wallet), UINT128_MAX, UINT128_MAX)])
    multi = _encode(manager, "multicall", [[bytes.fromhex(dec[2:]), bytes.fromhex(col[2:])]])
    tx = {"chainId": cfg.chain_id, "to": cfg.position_manager, "from": Web3.to_checksum_address(wallet), "data": multi, "value": "0x0"}
    simulation = {"ok": False, "method": "eth_call"}
    try:
        raw = w3.eth.call({"to": Web3.to_checksum_address(cfg.position_manager), "from": Web3.to_checksum_address(wallet), "data": multi})
        simulation = {"ok": True, "method": "eth_call", "return_data": _hex(raw)}
    except Exception as exc:
        simulation = {"ok": False, "method": "eth_call", "error": str(exc)}
    return {"call": tx, "simulation": simulation, "manager": cfg.position_manager, "token_id": str(token_id), "liquidity": str(liquidity), "slippage_bps": slip, "amount0_min_raw": str(min0), "amount1_min_raw": str(min1), "deadline": deadline}
