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

# --- v0.8 manual-wallet position opening ---------------------------------
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from .pool_chain import read_v3_pool_metadata
from .v3_math import display_range_to_ticks, quote_paired_amount

ALLOWANCE_ABI=[{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
APPROVE_ABI=[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}]
MINT_ABI=[{"inputs":[{"components":[{"name":"token0","type":"address"},{"name":"token1","type":"address"},{"name":"fee","type":"uint24"},{"name":"tickLower","type":"int24"},{"name":"tickUpper","type":"int24"},{"name":"amount0Desired","type":"uint256"},{"name":"amount1Desired","type":"uint256"},{"name":"amount0Min","type":"uint256"},{"name":"amount1Min","type":"uint256"},{"name":"recipient","type":"address"},{"name":"deadline","type":"uint256"}],"name":"params","type":"tuple"}],"name":"mint","outputs":[{"name":"tokenId","type":"uint256"},{"name":"liquidity","type":"uint128"},{"name":"amount0","type":"uint256"},{"name":"amount1","type":"uint256"}],"stateMutability":"payable","type":"function"}]
WETH_ABI=[{"inputs":[],"name":"deposit","outputs":[],"stateMutability":"payable","type":"function"}]


def _raw_units(amount: float, decimals: int) -> int:
    return int((Decimal(str(max(0.0,float(amount)))) * (Decimal(10) ** int(decimals))).to_integral_value(rounding=ROUND_FLOOR))


def _tick_from_raw_ratio(raw_token1_per_token0: float) -> float:
    if raw_token1_per_token0 <= 0: raise ValueError("Range price must be positive")
    return math.log(raw_token1_per_token0) / math.log(1.0001)


def _ticks_from_display_range(meta: dict[str,Any], lower: float, upper: float) -> tuple[int,int]:
    return display_range_to_ticks(meta, lower, upper)


def _native_wrap_shortfall(desired_wrapped: float, current_wrapped: float) -> float:
    return max(0.0,float(desired_wrapped)-float(current_wrapped))


def scale_quote_to_capital(*, amount0: float, amount1: float, price0_usd: float, price1_usd: float, capital_usd: float) -> tuple[float,float]:
    unit_value=float(amount0)*float(price0_usd)+float(amount1)*float(price1_usd)
    if unit_value<=0 or float(capital_usd)<=0:
        raise ValueError("Current token prices cannot size this capital budget")
    scale=float(capital_usd)/unit_value
    return float(amount0)*scale,float(amount1)*scale


def quote_open_position_amounts(*, chain: str, pool_address: str, lower_price: float, upper_price: float, known_side: int, known_amount: float) -> dict[str,Any]:
    meta=read_v3_pool_metadata(chain,pool_address)
    if not meta.get("ok"):
        raise ValueError(f"Pool metadata unavailable: {meta.get('error')}")
    quote=quote_paired_amount(meta,lower=lower_price,upper=upper_price,known_side=known_side,known_amount=known_amount)
    return {"ok":True,"chain":chain_config(chain).key,"pool":meta,"quote":quote}


def build_open_position(*, chain: str, pool_address: str, wallet: str, lower_price: float, upper_price: float, amount0: float, amount1: float, slippage_bps: int = 100, ttl_seconds: int = 1200, wrap_native_amount: float = 0.0) -> dict[str,Any]:
    cfg=chain_config(chain)
    if not Web3.is_address(wallet): raise ValueError("Configured wallet is invalid")
    meta=read_v3_pool_metadata(chain,pool_address)
    if not meta.get("ok"): raise ValueError(f"Pool metadata unavailable: {meta.get('error')}")
    t0,t1=meta["token0"],meta["token1"]; fee=int(meta["fee_tier"]); tick_lower,tick_upper=_ticks_from_display_range(meta,float(lower_price),float(upper_price))
    a0=_raw_units(amount0,int(t0["decimals"])); a1=_raw_units(amount1,int(t1["decimals"]));
    if a0<=0 and a1<=0: raise ValueError("Enter at least one token amount")
    slip=max(0,min(5000,int(slippage_bps))); min0=a0*(10000-slip)//10000; min1=a1*(10000-slip)//10000; deadline=int(time.time())+max(60,int(ttl_seconds))
    w3=build_read_only_web3(cfg.rpc_url()); owner=Web3.to_checksum_address(wallet); manager=Web3.to_checksum_address(cfg.position_manager)
    approval_calls=[]; allowances={}
    for token_info,desired in ((t0,a0),(t1,a1)):
        if desired<=0: continue
        token=w3.eth.contract(address=Web3.to_checksum_address(token_info["address"]),abi=ALLOWANCE_ABI+APPROVE_ABI)
        allowance=int(token.functions.allowance(owner,manager).call()); allowances[token_info["symbol"]]=str(allowance)
        if allowance<desired:
            data=_encode(token,"approve",[manager,desired])
            approval_calls.append({"chainId":cfg.chain_id,"to":token_info["address"],"from":owner,"data":data,"value":"0x0","purpose":f"APPROVE_{token_info['symbol']}","amount_raw":str(desired)})
    wrap_call=None
    if wrap_native_amount>0:
        wrapped=str(cfg.wrapped_native or "").lower(); token_addresses={str(t0["address"]).lower(),str(t1["address"]).lower()}
        if not wrapped or wrapped not in token_addresses: raise ValueError("Pool does not use the chain wrapped-native token")

        # "Use ETH automatically" means cover only the WETH shortfall. The old
        # implementation rebuilt the full wrap prerequisite after a successful wrap,
        # trapping Execution Desk forever in AWAITING_PREREQUISITES.
        desired_wrapped=0.0
        if str(t0["address"]).lower()==wrapped:
            desired_wrapped=float(amount0)
        elif str(t1["address"]).lower()==wrapped:
            desired_wrapped=float(amount1)

        weth=w3.eth.contract(
            address=Web3.to_checksum_address(cfg.wrapped_native),
            abi=WETH_ABI+[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}],
        )
        wrapped_balance_raw=int(weth.functions.balanceOf(owner).call())
        wrapped_decimals=int(t0["decimals"]) if str(t0["address"]).lower()==wrapped else int(t1["decimals"])
        wrapped_balance=wrapped_balance_raw/(10**wrapped_decimals)
        shortfall=_native_wrap_shortfall(desired_wrapped,wrapped_balance)
        if shortfall>1e-12:
            data=_encode(weth,"deposit",[])
            wrap_call={
                "chainId":cfg.chain_id,"to":cfg.wrapped_native,"from":owner,"data":data,
                "value":hex(w3.to_wei(float(shortfall),"ether")),"purpose":"WRAP_NATIVE",
                "required_wrapped":desired_wrapped,"current_wrapped":wrapped_balance,"shortfall":shortfall,
            }
    manager_c=w3.eth.contract(address=manager,abi=MINT_ABI)
    params=(Web3.to_checksum_address(t0["address"]),Web3.to_checksum_address(t1["address"]),fee,tick_lower,tick_upper,a0,a1,min0,min1,owner,deadline)
    mint_data=_encode(manager_c,"mint",[params]); mint_call={"chainId":cfg.chain_id,"to":manager,"from":owner,"data":mint_data,"value":"0x0","purpose":"MINT_POSITION"}
    simulation={"ok":False,"method":"eth_call","status":"AWAITING_PREREQUISITES" if (approval_calls or wrap_call) else "READY"}
    gas_estimate=None
    if not approval_calls and not wrap_call:
        try:
            raw=w3.eth.call({"to":manager,"from":owner,"data":mint_data}); gas_estimate=int(w3.eth.estimate_gas({"to":manager,"from":owner,"data":mint_data})); simulation={"ok":True,"method":"eth_call","return_data":_hex(raw),"status":"READY"}
        except Exception as exc: simulation={"ok":False,"method":"eth_call","error":str(exc),"status":"SIMULATION_FAILED"}
    return {"ok":True,"chain":cfg.key,"pool":meta,"wallet":owner,"display_range":{"lower":lower_price,"upper":upper_price,"unit":(meta.get("price_lens") or {}).get("unit"),"label":(meta.get("price_lens") or {}).get("unit_label")},"ticks":{"lower":tick_lower,"upper":tick_upper,"spacing":meta.get("tick_spacing")},"amounts":{"token0":amount0,"token1":amount1,"token0_raw":str(a0),"token1_raw":str(a1)},"allowances":allowances,"wrap_call":wrap_call,"approval_calls":approval_calls,"mint_call":mint_call,"simulation":simulation,"gas_estimate":gas_estimate,"slippage_bps":slip,"deadline":deadline,"execution_order":(["WRAP_NATIVE"] if wrap_call else [])+[c["purpose"] for c in approval_calls]+["MINT_POSITION"],"manual_wallet_required":True,"signing_server_side":False}
