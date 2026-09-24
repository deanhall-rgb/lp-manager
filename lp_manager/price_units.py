from __future__ import annotations

from typing import Any

STABLES={"USDC","USDT","DAI","USDS","USDBC","USDG","USD+","FRAX","LUSD","GHO"}
ETHS={"WETH","ETH"}
BTCS={"WBTC","BTC"}


def tick_token1_per_token0(tick: int, dec0: int, dec1: int) -> float:
    try: return (1.0001 ** int(tick)) * (10 ** (int(dec0)-int(dec1)))
    except OverflowError: return float("inf") if tick>0 else 0.0


def display_lens(sym0: str, sym1: str, raw_lower: float, raw_upper: float, raw_current: float) -> dict[str,Any]:
    """Choose an explicit human price lens without losing canonical tick meaning.

    raw_* are token1 per token0. We prefer USD-stable per risk asset, then risk
    token per ETH for volatile ETH pairs (matching the user's DELTA/ETH mental
    model), then the canonical token1/token0 ratio.
    """
    a=str(sym0 or "TOKEN0").upper(); b=str(sym1 or "TOKEN1").upper()
    invert=False
    # Stablecoin is the preferred quote: USDC per WETH, not WETH per USDC.
    if a in STABLES and b not in STABLES:
        invert=True
    # For ETH vs volatile token, default to volatile tokens per ETH. This makes
    # WETH/DELTA read as ~200k DELTA per WETH rather than a tiny inverse number.
    elif b in ETHS and a not in STABLES|ETHS:
        invert=True
    # For BTC vs stable, same stable quote convention handled above.
    if invert:
        lo=1/raw_upper if raw_upper else 0.0
        hi=1/raw_lower if raw_lower else 0.0
        cur=1/raw_current if raw_current else 0.0
        numerator=a; denominator=b
    else:
        lo=raw_lower; hi=raw_upper; cur=raw_current
        numerator=b; denominator=a
    if lo>hi: lo,hi=hi,lo
    return {
        "lower":lo,"upper":hi,"current":cur,"inverted":invert,
        "unit":f"{numerator}_PER_{denominator}",
        "unit_label":f"{numerator} per {denominator}",
        "token0_symbol":a,"token1_symbol":b,
        "canonical_unit":f"{b}_PER_{a}",
        "canonical_lower":raw_lower,"canonical_upper":raw_upper,"canonical_current":raw_current,
    }
