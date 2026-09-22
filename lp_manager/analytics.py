from __future__ import annotations

import math
import time
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def range_metrics(position: dict[str, Any]) -> dict[str, Any]:
    lower = _f(position.get("lower_price"))
    upper = _f(position.get("upper_price"))
    current = _f(position.get("current_price"))
    if lower <= 0 or upper <= lower or current <= 0:
        return {
            "range_state": "UNKNOWN",
            "distance_lower_pct": None,
            "distance_upper_pct": None,
            "progress_pct": None,
            "nearest_edge_pct": None,
        }
    below = (current / lower - 1.0) * 100.0
    above = (upper / current - 1.0) * 100.0
    progress = (current - lower) / (upper - lower) * 100.0
    if current < lower:
        state = "OUT_BELOW"
    elif current > upper:
        state = "OUT_ABOVE"
    else:
        state = "IN_RANGE"
    nearest = min(abs(below), abs(above)) if state == "IN_RANGE" else 0.0
    return {
        "range_state": state,
        "distance_lower_pct": below,
        "distance_upper_pct": above,
        "progress_pct": max(0.0, min(100.0, progress)),
        "nearest_edge_pct": nearest,
    }


def position_economics(position: dict[str, Any]) -> dict[str, float]:
    capital = _f(position.get("capital_value"))
    current = _f(position.get("current_value"))
    unclaimed = _f(position.get("unclaimed_fees"))
    realised = _f(position.get("realised_fees"))
    il = _f(position.get("estimated_il"))
    gas = _f(position.get("gas_costs"))
    principal_change = current - capital
    net = principal_change + unclaimed + realised + il - gas
    return {
        "principal_change": principal_change,
        "fees_total": unclaimed + realised,
        "net_profit": net,
        "net_return_pct": (net / capital * 100.0) if capital > 0 else 0.0,
    }


def portfolio_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    open_rows = [p for p in positions if str(p.get("status")).upper() == "OPEN"]
    closed_rows = [p for p in positions if str(p.get("status")).upper() == "CLOSED"]
    deployed = sum(_f(p.get("current_value")) for p in open_rows)
    capital = sum(_f(p.get("capital_value")) for p in open_rows)
    unclaimed = sum(_f(p.get("unclaimed_fees")) for p in open_rows)
    fees_today = sum(_f(p.get("fees_today")) for p in open_rows)
    fees_7d = sum(_f(p.get("fees_7d")) for p in open_rows)
    fees_30d = sum(_f(p.get("fees_30d")) for p in open_rows)
    total_net = sum(position_economics(p)["net_profit"] for p in positions)
    in_range = sum(1 for p in open_rows if range_metrics(p)["range_state"] == "IN_RANGE")
    weighted_apr = (
        sum(_f(p.get("apr_current")) * _f(p.get("current_value")) for p in open_rows) / deployed
        if deployed > 0 else 0.0
    )
    return {
        "open_positions": len(open_rows),
        "closed_positions": len(closed_rows),
        "capital_deployed": deployed,
        "cost_basis_open": capital,
        "unclaimed_fees": unclaimed,
        "fees_today": fees_today,
        "fees_7d": fees_7d,
        "fees_30d": fees_30d,
        "net_profit_all_time": total_net,
        "current_weighted_apr": weighted_apr,
        "earning_pct": (in_range / len(open_rows) * 100.0) if open_rows else 0.0,
        "out_of_range_count": len(open_rows) - in_range,
        "generated_at": time.time(),
    }


def portfolio_by_sleeve(positions: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for position in positions:
        if str(position.get("status") or "").upper() != "OPEN":
            continue
        sleeve = str(position.get("strategy_sleeve") or "TACTICAL_CAMPAIGN").upper()
        bucket = out.setdefault(sleeve, {
            "positions": 0, "capital_deployed": 0.0, "fees_today": 0.0,
            "fees_30d": 0.0, "unclaimed_fees": 0.0, "net_profit": 0.0,
        })
        bucket["positions"] += 1
        bucket["capital_deployed"] += _f(position.get("current_value"))
        bucket["fees_today"] += _f(position.get("fees_today"))
        bucket["fees_30d"] += _f(position.get("fees_30d"))
        bucket["unclaimed_fees"] += _f(position.get("unclaimed_fees"))
        bucket["net_profit"] += position_economics(position)["net_profit"]
    total = sum(row["capital_deployed"] for row in out.values())
    for row in out.values():
        row["capital_share_pct"] = (row["capital_deployed"] / total * 100.0) if total else 0.0
    return out


def enrich_position(position: dict[str, Any]) -> dict[str, Any]:
    out = dict(position)
    out["range"] = range_metrics(position)
    out["economics"] = position_economics(position)
    return out
