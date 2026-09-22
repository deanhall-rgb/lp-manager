from __future__ import annotations

from typing import Any


def audit_range_outcome(
    *,
    lower: float,
    upper: float,
    entry_price: float,
    future_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    if lower <= 0 or upper <= lower or entry_price <= 0:
        raise ValueError("invalid range/entry")
    prices = [float(r.get("close") or r.get("current_price") or 0.0) for r in future_rows]
    prices = [p for p in prices if p > 0]
    if not prices:
        return {"samples": 0, "classification": "NO_FUTURE_DATA"}
    in_flags = [lower <= p <= upper for p in prices]
    below = [p < lower for p in prices]
    above = [p > upper for p in prices]
    first_exit = next((i for i, ok in enumerate(in_flags) if not ok), None)
    reentered = False
    if first_exit is not None:
        reentered = any(in_flags[first_exit + 1:])
    end = prices[-1]
    return {
        "samples": len(prices),
        "ending_price": end,
        "ending_return_pct": round((end / entry_price - 1.0) * 100.0, 3),
        "max_favourable_move_pct": round((max(prices) / entry_price - 1.0) * 100.0, 3),
        "max_adverse_move_pct": round((min(prices) / entry_price - 1.0) * 100.0, 3),
        "in_range_pct": round(sum(in_flags) / len(in_flags) * 100.0, 3),
        "below_pct": round(sum(below) / len(below) * 100.0, 3),
        "above_pct": round(sum(above) / len(above) * 100.0, 3),
        "first_exit_index": first_exit,
        "reentered_after_exit": reentered,
        "classification": "STAYED_IN_RANGE" if first_exit is None else "EXITED_AND_REENTERED" if reentered else "EXITED_NO_REENTRY",
    }


def audit_decision(decision: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    action = str(decision.get("action") or "")
    classification = str(outcome.get("classification") or "")
    in_range_pct = float(outcome.get("in_range_pct") or 0.0)
    if "HOLD" in action:
        verdict = "SUPPORTED" if classification == "STAYED_IN_RANGE" or (classification == "EXITED_AND_REENTERED" and in_range_pct >= 60) else "QUESTIONABLE"
    elif any(x in action for x in ("PREPARE", "RECENTER", "EXIT", "REVIEW")):
        verdict = "SUPPORTED" if classification != "STAYED_IN_RANGE" else "EARLY_OR_UNNECESSARY"
    else:
        verdict = "UNSCORED"
    return {"action": action, "verdict": verdict, "outcome": outcome}
