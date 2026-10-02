from __future__ import annotations

import time
from typing import Any

from .product import OPPORTUNITY_SCHEMA_VERSION

PROTOCOL_UNISWAP_V3 = "UNISWAP_V3"
PROTOCOL_UNISWAP_V4 = "UNISWAP_V4"
KNOWN_PROTOCOLS = frozenset({PROTOCOL_UNISWAP_V3, PROTOCOL_UNISWAP_V4})

ANALYSIS_DISCOVERED = "DISCOVERED"
ANALYSIS_SCREENED = "SCREENED"
ANALYSIS_QUEUED = "QUEUED_FOR_ANALYSIS"
ANALYSIS_ANALYSED = "ANALYSED"
ANALYSIS_STALE = "STALE"
ANALYSIS_REJECTED = "REJECTED"
ANALYSIS_STATES = frozenset({
    ANALYSIS_DISCOVERED, ANALYSIS_SCREENED, ANALYSIS_QUEUED,
    ANALYSIS_ANALYSED, ANALYSIS_STALE, ANALYSIS_REJECTED,
})


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _token(value: Any) -> dict[str, Any]:
    raw = dict(value or {})
    symbol = str(raw.get("symbol") or "").strip()
    address = str(raw.get("address") or "").strip()
    if symbol:
        raw["symbol"] = symbol.upper()
    if address:
        raw["address"] = address
    return raw


def normalise_protocol(
    protocol: Any = None,
    *,
    version: Any = None,
    protocol_guess: Any = None,
    dex_id: Any = None,
) -> str:
    candidates = [
        str(protocol or "").upper(),
        str(protocol_guess or "").upper(),
        str(version or "").upper(),
        str(dex_id or "").upper(),
    ]
    for value in candidates:
        if "V4" in value and ("UNISWAP" in value or value == "V4"):
            return PROTOCOL_UNISWAP_V4
        if "V3" in value and ("UNISWAP" in value or value == "V3"):
            return PROTOCOL_UNISWAP_V3
    # Preserve a provider-specific protocol rather than pretending it is Uniswap.
    explicit = str(protocol or protocol_guess or "").strip().upper()
    return explicit or "UNKNOWN"


def protocol_version(protocol: Any = None, *, version: Any = None, protocol_guess: Any = None, dex_id: Any = None) -> str:
    p = normalise_protocol(protocol, version=version, protocol_guess=protocol_guess, dex_id=dex_id)
    if p == PROTOCOL_UNISWAP_V4:
        return "V4"
    if p == PROTOCOL_UNISWAP_V3:
        return "V3"
    v = str(version or "").strip().upper()
    return v if v in {"V2", "V3", "V4"} else "UNKNOWN"


def opportunity_id(chain: Any, protocol: Any, pool_address: Any) -> str:
    return f"{str(chain or '').upper()}:{str(protocol or 'UNKNOWN').upper()}:{str(pool_address or '').lower()}"


def freshness_state(updated_at: Any, *, now: float | None = None, fresh_seconds: float = 300.0, stale_seconds: float = 1800.0) -> tuple[str, float | None]:
    ts = _f(updated_at)
    if ts <= 0:
        return "UNKNOWN", None
    age = max(0.0, float(now if now is not None else time.time()) - ts)
    if age <= max(1.0, fresh_seconds):
        return "FRESH", age
    if age <= max(fresh_seconds, stale_seconds):
        return "AGING", age
    return "STALE", age


def canonical_opportunity(
    row: dict[str, Any],
    *,
    analysis_status: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Return the canonical opportunity contract while preserving legacy fields.

    v0.9.7.1 is deliberately compatibility-first: callers keep every existing
    field, while the new canonical fields give later leaderboard patches one
    stable cross-chain/V3/V4 shape to consume.
    """
    out = dict(row or {})
    chain = str(out.get("chain") or "").upper()
    address = str(out.get("pool_address") or out.get("address") or "").strip()
    protocol = normalise_protocol(
        out.get("protocol"),
        version=out.get("version"),
        protocol_guess=out.get("protocol_guess"),
        dex_id=out.get("dex_id"),
    )
    version = protocol_version(
        protocol,
        version=out.get("version"),
        protocol_guess=out.get("protocol_guess"),
        dex_id=out.get("dex_id"),
    )
    base = _token(out.get("base_token"))
    quote = _token(out.get("quote_token"))
    pair = str(out.get("pair") or "").strip()
    if not pair and base.get("symbol") and quote.get("symbol"):
        pair = f"{base['symbol']}/{quote['symbol']}"

    market_updated_at = _f(out.get("source_updated_at")) or _f(out.get("market_updated_at"))
    analysed_at = _f(out.get("analysed_at")) or _f(out.get("analysis_updated_at"))
    market_state, market_age = freshness_state(market_updated_at, now=now)
    analysis_state, analysis_age = freshness_state(analysed_at, now=now, fresh_seconds=900.0, stale_seconds=3600.0)

    state = str(
        analysis_status
        or out.get("analysis_status")
        or (ANALYSIS_ANALYSED if analysed_at > 0 else ANALYSIS_DISCOVERED)
    ).upper()
    if state not in ANALYSIS_STATES:
        state = ANALYSIS_DISCOVERED

    out.update({
        "opportunity_schema_version": OPPORTUNITY_SCHEMA_VERSION,
        "opportunity_id": opportunity_id(chain, protocol, address),
        "chain": chain,
        "protocol": protocol,
        "protocol_version": version,
        "version": version if version != "UNKNOWN" else out.get("version"),
        "pool_address": address,
        "pair": pair,
        "base_token": base,
        "quote_token": quote,
        "tvl_usd": _f(out.get("tvl_usd")),
        "volume_24h_usd": _f(out.get("volume_24h_usd")),
        "analysis_status": state,
        "freshness": {
            "market": market_state,
            "market_age_seconds": round(market_age, 1) if market_age is not None else None,
            "analysis": analysis_state,
            "analysis_age_seconds": round(analysis_age, 1) if analysis_age is not None else None,
            "market_updated_at": market_updated_at or None,
            "analysed_at": analysed_at or None,
        },
    })
    return out
