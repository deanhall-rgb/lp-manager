from __future__ import annotations

from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try: return float(v)
    except Exception: return default


def pool_price_lens(pool: dict[str, Any], *, lower: float | None = None, upper: float | None = None, current: float | None = None) -> dict[str, Any]:
    base=(pool.get("base_token") or {})
    quote=(pool.get("quote_token") or {})
    base_symbol=str(base.get("symbol") or "BASE")
    quote_symbol=str(quote.get("symbol") or "QUOTE")
    spot=_f(current, _f(pool.get("base_token_price_usd")))
    mc=_f(pool.get("market_cap_usd"))
    fdv=_f(pool.get("fdv_usd"))
    supply=None
    supply_source=None
    if mc > 0 and spot > 0:
        supply=mc/spot; supply_source="MARKET_CAP_IMPLIED"
    elif fdv > 0 and spot > 0:
        supply=fdv/spot; supply_source="FDV_IMPLIED"
    onchain_lens=((pool.get("onchain") or {}).get("price_lens") or {})
    explicit_unit=str(pool.get("price_unit") or onchain_lens.get("unit") or "")
    explicit_label=str(pool.get("price_unit_label") or onchain_lens.get("unit_label") or "")
    if not explicit_unit and base_symbol and quote_symbol:
        explicit_unit=f"{quote_symbol.upper()}_PER_{base_symbol.upper()}"
        explicit_label=f"{quote_symbol.upper()} per {base_symbol.upper()}"
    result={
        "base_symbol":base_symbol,"quote_symbol":quote_symbol,
        "spot_token_price_usd":spot,
        "market_cap_usd":mc or None,"fdv_usd":fdv or None,
        "implied_supply":supply,"supply_source":supply_source,
        "unit":explicit_unit,"unit_label":explicit_label,
        "primary_display":"MARKET_CAP" if supply and base_symbol.upper() not in {"WETH","ETH","WBTC","BTC","USDC","USDT","DAI","USDG"} else "TOKEN_PRICE",
    }
    if supply:
        if lower and lower > 0: result["lower_market_cap_usd"]=_f(lower)*supply
        if upper and upper > 0: result["upper_market_cap_usd"]=_f(upper)*supply
        if spot > 0: result["current_market_cap_usd"]=spot*supply
    return result
