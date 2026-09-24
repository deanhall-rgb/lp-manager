from __future__ import annotations

from typing import Any

STABLES={"USDC","USDT","DAI","USDS","USDBC","USDG","USD+","FRAX","LUSD","GHO"}
ETHS={"WETH","ETH"}
BTCS={"WBTC","BTC"}


def tick_token1_per_token0(tick: int, dec0: int, dec1: int) -> float:
    try:
        return (1.0001 ** int(tick)) * (10 ** (int(dec0)-int(dec1)))
    except OverflowError:
        return float("inf") if tick > 0 else 0.0


def validate_display_lens(lens: dict[str, Any]) -> dict[str, Any]:
    """Sanity-check the human execution lens before profitability code can use it.

    This catches the V0.8.6 USDG/WETH defect where a token's ~1 USD mark was fed
    into the range engine as though it were ~1 USDG per WETH.
    """
    current=float(lens.get("current") or 0)
    lower=float(lens.get("lower") or current or 0)
    upper=float(lens.get("upper") or current or 0)
    unit=str(lens.get("unit") or "")
    a=str(lens.get("token0_symbol") or "").upper()
    b=str(lens.get("token1_symbol") or "").upper()
    reasons=[]
    if current <= 0 or lower <= 0 or upper <= 0:
        reasons.append("NON_POSITIVE_PRICE")
    if lower > upper:
        reasons.append("INVERTED_BOUNDS")

    symbols={a,b}
    if symbols & ETHS and symbols & STABLES:
        stable=next(iter(symbols & STABLES))
        eth=next(iter(symbols & ETHS))
        expected=f"{stable}_PER_{eth}"
        if unit != expected:
            reasons.append(f"EXPECTED_{expected}")
        # Wide enough for historical/future ETH prices but rejects ~1 stable/ETH.
        if current > 0 and not 100.0 <= current <= 100_000.0:
            reasons.append("IMPLAUSIBLE_STABLE_PER_ETH_PRICE")
    elif symbols & BTCS and symbols & STABLES:
        stable=next(iter(symbols & STABLES))
        btc=next(iter(symbols & BTCS))
        expected=f"{stable}_PER_{btc}"
        if unit != expected:
            reasons.append(f"EXPECTED_{expected}")
        if current > 0 and not 1_000.0 <= current <= 5_000_000.0:
            reasons.append("IMPLAUSIBLE_STABLE_PER_BTC_PRICE")

    return {
        "valid": not reasons,
        "reasons": reasons,
        "unit": unit,
        "current": current,
    }


def assert_sane_display_lens(lens: dict[str, Any]) -> dict[str, Any]:
    check=validate_display_lens(lens)
    if not check["valid"]:
        raise ValueError(
            "Implausible pool execution price/unit: "
            + ", ".join(check["reasons"])
            + f" ({check['current']} {check['unit']})"
        )
    return lens


def display_lens(sym0: str, sym1: str, raw_lower: float, raw_upper: float, raw_current: float) -> dict[str,Any]:
    """Choose an explicit human price lens without losing canonical tick meaning.

    raw_* are token1 per token0. We prefer USD-stable per risk asset, then risk
    token per ETH for volatile ETH pairs (matching the operator's execution lens),
    then the canonical token1/token0 ratio.
    """
    a=str(sym0 or "TOKEN0").upper()
    b=str(sym1 or "TOKEN1").upper()
    invert=False
    if a in STABLES and b not in STABLES:
        invert=True
    elif b in ETHS and a not in STABLES|ETHS:
        invert=True

    if invert:
        lo=1/raw_upper if raw_upper else 0.0
        hi=1/raw_lower if raw_lower else 0.0
        cur=1/raw_current if raw_current else 0.0
        numerator=a
        denominator=b
    else:
        lo=raw_lower
        hi=raw_upper
        cur=raw_current
        numerator=b
        denominator=a
    if lo>hi:
        lo,hi=hi,lo

    result={
        "lower":lo,"upper":hi,"current":cur,"inverted":invert,
        "unit":f"{numerator}_PER_{denominator}",
        "unit_label":f"{numerator} per {denominator}",
        "token0_symbol":a,"token1_symbol":b,
        "canonical_unit":f"{b}_PER_{a}",
        "canonical_lower":raw_lower,"canonical_upper":raw_upper,"canonical_current":raw_current,
    }
    result["validation"]=validate_display_lens(result)
    return result
