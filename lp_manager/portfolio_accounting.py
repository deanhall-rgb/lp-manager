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

    recorded_cost = max(0.0, _f(position.get("capital_value")))
    current_principal = max(0.0, _f(position.get("current_value") or snapshot.get("current_value_usd")))
    unclaimed = max(0.0, _f(position.get("unclaimed_fees") or snapshot.get("unclaimed_fees_usd")))
    realised = max(0.0, _f(position.get("realised_fees")))
    tracked = max(0.0, _f(tracker.get("cumulative_earned_usd")))
    fees_earned = max(tracked, unclaimed + realised)
    gas = max(0.0, _f(position.get("gas_costs")))

    amount0 = max(0.0, _f(entry.get("token0_amount")))
    amount1 = max(0.0, _f(entry.get("token1_amount")))
    entry_price0=max(0.0,_f(entry.get("token0_price_usd")))
    entry_price1=max(0.0,_f(entry.get("token1_price_usd")))
    entry_complete=bool(entry.get("basis_complete")) or (
        (amount0>0 or amount1>0)
        and (amount0<=0 or entry_price0>0)
        and (amount1<=0 or entry_price1>0)
        and _f(entry.get("entry_value_usd"))>0
    )
    entry_cost=max(0.0,_f(entry.get("entry_value_usd")))
    cost=entry_cost if entry_complete and entry_cost>0 else recorded_cost
    basis_quality=str((entry.get("quality") if entry_complete else position.get("cost_basis_quality")) or position.get("cost_basis_quality") or "UNKNOWN").upper()
    basis_ready=bool(
        cost>0
        and (
            entry_complete
            or (
                not entry
                and basis_quality in {"VERIFIED_OPENING_BASIS","RECONCILED_REALIZED","HISTORICAL_RECONSTRUCTED"}
            )
        )
    )

    # Operator-facing P/L is intentionally simple and auditable:
    # current LP value + all tracked fees earned - verified opening capital.
    # Transaction costs remain a separate line so the user can see both gross LP
    # performance and true after-cost wealth without hidden deductions.
    strategy_wealth = current_principal + fees_earned
    gross_pnl = (strategy_wealth - cost) if basis_ready else None
    gross_return = (gross_pnl / cost * 100.0) if basis_ready and cost > 0 else None
    net_wealth_after_costs = strategy_wealth - gas
    net_pnl_after_costs = (net_wealth_after_costs - cost) if basis_ready else None
    net_return_after_costs = (net_pnl_after_costs / cost * 100.0) if basis_ready and cost > 0 else None

    price0 = max(0.0, _f(t0.get("price_usd")))
    price1 = max(0.0, _f(t1.get("price_usd")))
    hodl_value = amount0 * price0 + amount1 * price1 if (amount0 > 0 or amount1 > 0) and (amount0<=0 or price0>0) and (amount1<=0 or price1>0) else None
    lp_vs_hodl = strategy_wealth - hodl_value if hodl_value is not None else None
    lp_vs_hodl_pct = lp_vs_hodl / hodl_value * 100.0 if hodl_value and hodl_value > 0 else None

    opened=_f(entry.get("opened_at") or snapshot.get("opened_at") or position.get("opened_at"))
    opening_tx=entry.get("transaction_hash") or snapshot.get("opening_transaction_hash") or ""
    hodl_ready = hodl_value is not None and bool(opened) and bool(amount0>0 or amount1>0)
    quality = "STRONG" if basis_ready and hodl_ready else "PARTIAL" if (basis_ready or hodl_ready or opening_tx) else "INSUFFICIENT"

    return {
        "opened_at": opened,
        "opening_time_quality": "ONCHAIN_OPENING_TX" if opening_tx and opened else ("FIRST_OBSERVED" if opened else "UNKNOWN"),
        "opening_transaction_hash": opening_tx,
        "cost_basis_usd": round(cost, 4),
        "cost_basis_quality": (entry.get("quality") if entry_complete else position.get("cost_basis_quality")) or "UNKNOWN",
        "cost_basis_source": "ENTRY_EVIDENCE" if entry_complete and entry_cost>0 else "POSITION_RECORD",
        "basis_ready": basis_ready,
        "current_principal_usd": round(current_principal, 4),
        "fees_earned_usd": round(fees_earned, 4),
        "gas_costs_usd": round(gas, 4),
        "strategy_wealth_usd": round(strategy_wealth, 4),
        "lp_plus_fees_usd": round(strategy_wealth, 4),
        "absolute_pnl_incl_fees_usd": round(gross_pnl, 4) if gross_pnl is not None else None,
        "absolute_return_incl_fees_pct": round(gross_return, 4) if gross_return is not None else None,
        "net_wealth_after_costs_usd": round(net_wealth_after_costs, 4),
        "net_pnl_after_costs_usd": round(net_pnl_after_costs, 4) if net_pnl_after_costs is not None else None,
        "net_return_after_costs_pct": round(net_return_after_costs, 4) if net_return_after_costs is not None else None,
        "hodl_value_usd": round(hodl_value, 4) if hodl_value is not None else None,
        "lp_vs_hodl_usd": round(lp_vs_hodl, 4) if lp_vs_hodl is not None else None,
        "lp_vs_hodl_pct": round(lp_vs_hodl_pct, 4) if lp_vs_hodl_pct is not None else None,
        "entry_token0_amount": round(amount0, 12),
        "entry_token1_amount": round(amount1, 12),
        "entry_basis_complete": entry_complete,
        "quality": quality,
    }
