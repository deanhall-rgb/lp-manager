from __future__ import annotations

import math
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def position_accounting(
    position: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    tracker: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic LP-vs-HODL and absolute P/L accounting in source USD.

    Display conversion belongs to the money/FX layer. This function deliberately
    keeps source USD values so the audit trail is not damaged by later FX changes.
    """
    snapshot = snapshot or {}
    tracker = tracker or {}
    entry = dict(snapshot.get("entry_evidence") or {})
    t0 = dict(snapshot.get("token0") or {})
    t1 = dict(snapshot.get("token1") or {})

    cost = max(0.0, _f(position.get("capital_value") or entry.get("entry_value_usd")))
    current_principal = max(0.0, _f(position.get("current_value") or snapshot.get("current_value_usd")))
    unclaimed = max(0.0, _f(position.get("unclaimed_fees") or snapshot.get("unclaimed_fees_usd")))
    realised = max(0.0, _f(position.get("realised_fees")))
    tracked = max(0.0, _f(tracker.get("cumulative_earned_usd")))
    fees_earned = max(tracked, unclaimed + realised)
    gas = max(0.0, _f(position.get("gas_costs")))

    lp_plus_fees = current_principal + fees_earned - gas
    absolute_pnl = lp_plus_fees - cost if cost > 0 else 0.0
    absolute_return = absolute_pnl / cost * 100.0 if cost > 0 else 0.0

    amount0 = max(0.0, _f(entry.get("token0_amount")))
    amount1 = max(0.0, _f(entry.get("token1_amount")))
    price0 = max(0.0, _f(t0.get("price_usd")))
    price1 = max(0.0, _f(t1.get("price_usd")))
    hodl_value = amount0 * price0 + amount1 * price1 if (amount0 > 0 or amount1 > 0) and price0 > 0 and price1 > 0 else None
    lp_vs_hodl = lp_plus_fees - hodl_value if hodl_value is not None else None
    lp_vs_hodl_pct = lp_vs_hodl / hodl_value * 100.0 if hodl_value and hodl_value > 0 else None

    strong_basis = str(position.get("cost_basis_quality") or "").upper() not in {"", "UNKNOWN", "FIRST_OBSERVED"}
    hodl_ready = hodl_value is not None and bool(entry.get("opened_at") or position.get("opened_at"))
    quality = "STRONG" if strong_basis and hodl_ready else "PARTIAL" if cost > 0 else "INSUFFICIENT"

    return {
        "opened_at": _f(entry.get("opened_at") or position.get("opened_at")),
        "opening_transaction_hash": entry.get("transaction_hash") or snapshot.get("opening_transaction_hash") or "",
        "cost_basis_usd": round(cost, 4),
        "cost_basis_quality": position.get("cost_basis_quality") or entry.get("quality") or "UNKNOWN",
        "current_principal_usd": round(current_principal, 4),
        "fees_earned_usd": round(fees_earned, 4),
        "gas_costs_usd": round(gas, 4),
        "lp_plus_fees_usd": round(lp_plus_fees, 4),
        "absolute_pnl_incl_fees_usd": round(absolute_pnl, 4),
        "absolute_return_incl_fees_pct": round(absolute_return, 4),
        "hodl_value_usd": round(hodl_value, 4) if hodl_value is not None else None,
        "lp_vs_hodl_usd": round(lp_vs_hodl, 4) if lp_vs_hodl is not None else None,
        "lp_vs_hodl_pct": round(lp_vs_hodl_pct, 4) if lp_vs_hodl_pct is not None else None,
        "entry_token0_amount": round(amount0, 12),
        "entry_token1_amount": round(amount1, 12),
        "quality": quality,
    }
