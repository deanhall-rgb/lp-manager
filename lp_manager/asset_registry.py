from __future__ import annotations

"""Canonical asset classifications used across discovery, pricing and strategy.

Keep symbol policy in one place. Provider token identity is still address-based;
these sets exist only for behavioural classes such as stable-quoted execution,
major-asset display and Core inventory policy.
"""

STABLE_SYMBOLS = frozenset({
    "USDC", "USDT", "USDT0", "DAI", "USDG", "USDS", "USDBC",
    "USD+", "FRAX", "GHO", "LUSD",
})

ETH_SYMBOLS = frozenset({"ETH", "WETH"})
BTC_SYMBOLS = frozenset({"BTC", "WBTC"})

# Assets currently treated as high-quality major inventory for Core pair policy.
# Preserve the v0.9.6 semantics: network governance/native tokens such as ARB or
# POL are not automatically promoted to Core simply because they are well known.
RISK_MAJOR_SYMBOLS = frozenset(ETH_SYMBOLS | BTC_SYMBOLS)
CORE_MAJOR_SYMBOLS = frozenset(RISK_MAJOR_SYMBOLS | STABLE_SYMBOLS)

# Assets for which market-cap conversion is misleading/unnecessary in Profit Lab.
# These should stay in executable pool-price units.
NETWORK_MAJOR_SYMBOLS = frozenset({
    "ETH", "WETH", "BTC", "WBTC",
    "POL", "WPOL", "MATIC", "WMATIC",
    "ARB", "OP", "BNB", "WBNB", "AVAX", "WAVAX", "SOL", "WSOL",
})
PRICE_DISPLAY_SYMBOLS = frozenset(NETWORK_MAJOR_SYMBOLS | STABLE_SYMBOLS)


def normalise_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def is_stable_symbol(value: object) -> bool:
    return normalise_symbol(value) in STABLE_SYMBOLS


def is_eth_symbol(value: object) -> bool:
    return normalise_symbol(value) in ETH_SYMBOLS


def is_btc_symbol(value: object) -> bool:
    return normalise_symbol(value) in BTC_SYMBOLS


def is_core_major_symbol(value: object) -> bool:
    return normalise_symbol(value) in CORE_MAJOR_SYMBOLS


def should_use_market_cap_display(symbol: object) -> bool:
    """Only small/non-major campaign assets should default to market-cap display."""
    value = normalise_symbol(symbol)
    return bool(value) and value not in PRICE_DISPLAY_SYMBOLS
