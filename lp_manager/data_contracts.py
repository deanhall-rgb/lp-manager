from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict
from typing import Any


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


@dataclass(frozen=True)
class PoolSnapshot:
    chain: str
    protocol: str
    pair: str
    pool_address: str
    observed_at: float
    price: float
    tvl_usd: float
    volume_24h_usd: float
    fee_tier: int | None = None
    liquidity: float | None = None
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionSnapshot:
    chain: str
    protocol: str
    position_id: str
    observed_at: float
    current_price: float
    lower_price: float
    upper_price: float
    liquidity: float
    token0_amount: float | None = None
    token1_amount: float | None = None
    fees0_owed: float | None = None
    fees1_owed: float | None = None
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_pool_snapshot(snapshot: PoolSnapshot | dict[str, Any], *, now: float | None = None, max_age_seconds: float = 300.0) -> dict[str, Any]:
    row = snapshot.to_dict() if isinstance(snapshot, PoolSnapshot) else dict(snapshot)
    now = float(now or time.time())
    errors: list[str] = []
    warnings: list[str] = []

    for field in ("chain", "protocol", "pair", "pool_address", "source"):
        if not str(row.get(field) or "").strip():
            errors.append(f"MISSING_{field.upper()}")
    for field in ("observed_at", "price", "tvl_usd", "volume_24h_usd"):
        if not _finite(row.get(field)):
            errors.append(f"INVALID_{field.upper()}")

    observed = float(row.get("observed_at") or 0.0) if _finite(row.get("observed_at")) else 0.0
    age = max(0.0, now - observed) if observed > 0 else None
    if age is not None and age > max_age_seconds:
        errors.append("STALE_SNAPSHOT")
    if _finite(row.get("price")) and float(row.get("price") or 0) <= 0:
        errors.append("NONPOSITIVE_PRICE")
    if _finite(row.get("tvl_usd")) and float(row.get("tvl_usd") or 0) < 0:
        errors.append("NEGATIVE_TVL")
    if _finite(row.get("volume_24h_usd")) and float(row.get("volume_24h_usd") or 0) < 0:
        errors.append("NEGATIVE_VOLUME")
    if _finite(row.get("tvl_usd")) and float(row.get("tvl_usd") or 0) == 0:
        warnings.append("ZERO_TVL")
    if _finite(row.get("volume_24h_usd")) and float(row.get("volume_24h_usd") or 0) == 0:
        warnings.append("ZERO_VOLUME")

    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "age_seconds": age,
        "max_age_seconds": max_age_seconds,
        "source": row.get("source"),
    }


def validate_position_snapshot(snapshot: PositionSnapshot | dict[str, Any], *, now: float | None = None, max_age_seconds: float = 180.0) -> dict[str, Any]:
    row = snapshot.to_dict() if isinstance(snapshot, PositionSnapshot) else dict(snapshot)
    now = float(now or time.time())
    errors: list[str] = []
    for field in ("chain", "protocol", "position_id", "source"):
        if not str(row.get(field) or "").strip():
            errors.append(f"MISSING_{field.upper()}")
    for field in ("observed_at", "current_price", "lower_price", "upper_price", "liquidity"):
        if not _finite(row.get(field)):
            errors.append(f"INVALID_{field.upper()}")
    lower = float(row.get("lower_price") or 0.0) if _finite(row.get("lower_price")) else 0.0
    upper = float(row.get("upper_price") or 0.0) if _finite(row.get("upper_price")) else 0.0
    current = float(row.get("current_price") or 0.0) if _finite(row.get("current_price")) else 0.0
    if lower <= 0 or upper <= lower:
        errors.append("INVALID_RANGE")
    if current <= 0:
        errors.append("NONPOSITIVE_PRICE")
    observed = float(row.get("observed_at") or 0.0) if _finite(row.get("observed_at")) else 0.0
    age = max(0.0, now - observed) if observed > 0 else None
    if age is not None and age > max_age_seconds:
        errors.append("STALE_SNAPSHOT")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "age_seconds": age,
        "max_age_seconds": max_age_seconds,
        "source": row.get("source"),
    }
