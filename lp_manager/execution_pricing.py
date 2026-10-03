from __future__ import annotations

from typing import Any

from .asset_registry import is_stable_symbol


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def complete_pool_usd_marks(meta: dict[str, Any], marks: dict[str, float] | None) -> dict[str, Any]:
    """Complete missing token USD marks from trusted pool-ratio evidence.

    Execution auto-sizing only needs a current USD value for each token. When a
    provider has one side but not the other, the verified on-chain V3 price lens
    contains enough information to derive the missing mark without another
    provider request. Known USD stables may provide the one-dollar anchor.

    The function never invents two prices from an unanchored ratio: at least one
    token must have a provider USD mark or be a recognised USD stable.
    """
    marks = {str(k or "").lower(): _f(v) for k, v in dict(marks or {}).items()}
    t0 = dict(meta.get("token0") or {})
    t1 = dict(meta.get("token1") or {})
    tokens = [t0, t1]
    addresses = [str(t.get("address") or "").lower() for t in tokens]
    symbols = [str(t.get("symbol") or "").upper() for t in tokens]
    prices = [marks.get(addresses[0], 0.0), marks.get(addresses[1], 0.0)]
    sources = ["PROVIDER" if prices[0] > 0 else "MISSING", "PROVIDER" if prices[1] > 0 else "MISSING"]

    for i, symbol in enumerate(symbols):
        if prices[i] <= 0 and is_stable_symbol(symbol):
            prices[i] = 1.0
            sources[i] = "USD_STABLE_PARITY"

    lens = dict(meta.get("price_lens") or {})
    current = _f(lens.get("current"))
    unit = str(lens.get("unit") or "").upper()
    if current > 0 and "_PER_" in unit:
        numerator, denominator = [x.strip() for x in unit.split("_PER_", 1)]
        try:
            ni = symbols.index(numerator)
            di = symbols.index(denominator)
        except ValueError:
            ni = di = -1
        if ni >= 0 and di >= 0 and ni != di:
            # "QNT per WETH = 10" means one WETH is worth ten QNT:
            # USD(WETH) = 10 * USD(QNT).
            if prices[ni] > 0 and prices[di] <= 0:
                prices[di] = prices[ni] * current
                sources[di] = f"POOL_RATIO_FROM_{symbols[ni]}"
            elif prices[di] > 0 and prices[ni] <= 0:
                prices[ni] = prices[di] / current
                sources[ni] = f"POOL_RATIO_FROM_{symbols[di]}"

    completed = dict(marks)
    for address, price in zip(addresses, prices):
        if address and price > 0:
            completed[address] = price

    return {
        "marks": completed,
        "price0_usd": prices[0],
        "price1_usd": prices[1],
        "sources": {
            symbols[0] or "TOKEN0": sources[0],
            symbols[1] or "TOKEN1": sources[1],
        },
        "anchored": bool(prices[0] > 0 and prices[1] > 0),
    }
