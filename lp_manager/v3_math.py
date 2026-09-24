from __future__ import annotations

import math
from typing import Any

Q96 = 2 ** 96


def tick_to_sqrt_price_x96(tick: int) -> float:
    """Return sqrt(price_raw) * 2**96 for a Uniswap V3 tick.

    Float precision is sufficient for quoting/planning amounts. Contract calldata
    remains built from canonical integer ticks in live_v3_builder.
    """
    return (1.0001 ** (int(tick) / 2.0)) * Q96


def display_range_to_ticks(meta: dict[str, Any], lower: float, upper: float) -> tuple[int, int]:
    lens = meta.get("price_lens") or {}
    inverted = bool(lens.get("inverted"))
    d0 = int((meta.get("token0") or {}).get("decimals") or 18)
    d1 = int((meta.get("token1") or {}).get("decimals") or 18)
    spacing = max(1, int(meta.get("tick_spacing") or 1))
    if lower <= 0 or upper <= lower:
        raise ValueError("Invalid display range")
    display_raw = [1.0 / upper, 1.0 / lower] if inverted else [lower, upper]
    scalar = 10 ** (d0 - d1)
    raw0, raw1 = display_raw[0] / scalar, display_raw[1] / scalar
    if raw0 <= 0 or raw1 <= 0:
        raise ValueError("Range cannot be converted to pool ticks")
    lo_float = min(math.log(raw0) / math.log(1.0001), math.log(raw1) / math.log(1.0001))
    hi_float = max(math.log(raw0) / math.log(1.0001), math.log(raw1) / math.log(1.0001))
    lo = math.floor(lo_float / spacing) * spacing
    hi = math.ceil(hi_float / spacing) * spacing
    if lo >= hi:
        hi = lo + spacing
    return int(lo), int(hi)


def amounts_raw_per_liquidity(*, sqrt_price_x96: float, tick_lower: int, tick_upper: int) -> tuple[float, float]:
    """Human-independent raw token amounts required per unit of liquidity."""
    p = float(sqrt_price_x96)
    a = tick_to_sqrt_price_x96(tick_lower)
    b = tick_to_sqrt_price_x96(tick_upper)
    if a > b:
        a, b = b, a
    if p <= a:
        return (max(0.0, (b - a) * Q96 / (a * b)), 0.0)
    if p >= b:
        return (0.0, max(0.0, (b - a) / Q96))
    amount0 = (b - p) * Q96 / (p * b)
    amount1 = (p - a) / Q96
    return max(0.0, amount0), max(0.0, amount1)


def quote_paired_amount(
    meta: dict[str, Any], *, lower: float, upper: float, known_side: int, known_amount: float
) -> dict[str, Any]:
    """Quote the paired token amount for the current tick and chosen range.

    known_side is 0 or 1 and amount is in human token units. The result is a
    deterministic ratio quote; the mint is still simulated immediately before
    signing so stale-price mistakes cannot silently pass.
    """
    if known_side not in (0, 1):
        raise ValueError("known_side must be 0 or 1")
    amount = max(0.0, float(known_amount))
    tick_lower, tick_upper = display_range_to_ticks(meta, float(lower), float(upper))
    sqrt_p = float(meta.get("sqrt_price_x96") or 0)
    if sqrt_p <= 0:
        raise ValueError("Current pool sqrt price is unavailable")
    per0_raw, per1_raw = amounts_raw_per_liquidity(
        sqrt_price_x96=sqrt_p, tick_lower=tick_lower, tick_upper=tick_upper
    )
    d0 = int((meta.get("token0") or {}).get("decimals") or 18)
    d1 = int((meta.get("token1") or {}).get("decimals") or 18)
    per0 = per0_raw / (10 ** d0)
    per1 = per1_raw / (10 ** d1)
    if known_side == 0:
        if per0 <= 0:
            paired = 0.0
            note = "Current price is above the selected range; this position is token1-only at entry."
        else:
            liquidity_units = amount / per0 if per0 else 0.0
            paired = liquidity_units * per1
            note = "Balanced for the current price and selected V3 range."
        amount0, amount1 = amount, paired
    else:
        if per1 <= 0:
            paired = 0.0
            note = "Current price is below the selected range; this position is token0-only at entry."
        else:
            liquidity_units = amount / per1 if per1 else 0.0
            paired = liquidity_units * per0
            note = "Balanced for the current price and selected V3 range."
        amount0, amount1 = paired, amount
    return {
        "amount0": amount0,
        "amount1": amount1,
        "known_side": known_side,
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "price_lens": meta.get("price_lens") or {},
        "current_tick": meta.get("current_tick"),
        "note": note,
    }


def active_liquidity_share_for_capital(
    pool: dict[str, Any], *, capital_usd: float, lower: float, upper: float
) -> dict[str, Any] | None:
    """Estimate current active-liquidity share for a hypothetical V3 position.

    This is substantially better than capital/TVL for an in-range V3 quote,
    because fee share at the current tick is determined by active liquidity.
    It is still a proxy for future fee capture because swaps can move through
    other ticks during the horizon.
    """
    onchain = pool.get("onchain") or {}
    try:
        active_pool_liquidity = float(onchain.get("active_liquidity") or onchain.get("liquidity") or 0)
        sqrt_p = float(onchain.get("sqrt_price_x96") or 0)
        if active_pool_liquidity <= 0 or sqrt_p <= 0 or capital_usd <= 0:
            return None
        tick_lower, tick_upper = display_range_to_ticks(onchain, lower, upper)
        per0_raw, per1_raw = amounts_raw_per_liquidity(
            sqrt_price_x96=sqrt_p, tick_lower=tick_lower, tick_upper=tick_upper
        )
        t0 = onchain.get("token0") or {}
        t1 = onchain.get("token1") or {}
        d0, d1 = int(t0.get("decimals") or 18), int(t1.get("decimals") or 18)

        base = pool.get("base_token") or {}
        quote = pool.get("quote_token") or {}
        prices: dict[str, float] = {}
        if base.get("address"):
            prices[str(base.get("address")).lower()] = float(pool.get("base_token_price_usd") or 0)
        if quote.get("address"):
            prices[str(quote.get("address")).lower()] = float(pool.get("quote_token_price_usd") or 0)
        p0 = prices.get(str(t0.get("address") or "").lower(), 0.0)
        p1 = prices.get(str(t1.get("address") or "").lower(), 0.0)
        if p0 <= 0 or p1 <= 0:
            return None
        usd_per_liquidity = (per0_raw / (10 ** d0)) * p0 + (per1_raw / (10 ** d1)) * p1
        if usd_per_liquidity <= 0:
            return None
        position_liquidity = float(capital_usd) / usd_per_liquidity
        share = position_liquidity / (active_pool_liquidity + position_liquidity)
        return {
            "method": "CURRENT_ACTIVE_LIQUIDITY_PROXY",
            "position_liquidity": position_liquidity,
            "pool_active_liquidity": active_pool_liquidity,
            "share": max(0.0, min(1.0, share)),
            "share_pct": max(0.0, min(100.0, share * 100.0)),
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "usd_per_liquidity": usd_per_liquidity,
        }
    except Exception:
        return None
