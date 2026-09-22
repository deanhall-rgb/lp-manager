from __future__ import annotations

from typing import Any

from .portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN


def fee_action(
    *,
    sleeve: str,
    unclaimed_fees_value: float,
    collect_cost: float,
    reinvest_cost: float = 0.0,
    expected_apr_pct: float = 0.0,
    horizon_days: float = 30.0,
    bank_profit: bool | None = None,
    min_fee_to_cost_multiple: float = 5.0,
) -> dict[str, Any]:
    sleeve = str(sleeve or TACTICAL_CAMPAIGN).upper()
    fees = max(0.0, float(unclaimed_fees_value or 0.0))
    collect = max(0.0, float(collect_cost or 0.0))
    reinvest = max(0.0, float(reinvest_cost or 0.0))
    total_compound_cost = collect + reinvest
    ratio = fees / collect if collect > 0 else (999.0 if fees > 0 else 0.0)

    if fees <= 0:
        return {"action": "LEAVE", "reason": "NO_FEES", "economically_positive": False, "fee_to_collect_cost": ratio}
    if collect > 0 and ratio < min_fee_to_cost_multiple:
        return {"action": "LEAVE", "reason": "FEES_TOO_SMALL_VS_COLLECTION_COST", "economically_positive": False, "fee_to_collect_cost": round(ratio, 3)}

    if bank_profit is None:
        bank_profit = sleeve == TACTICAL_CAMPAIGN

    if bank_profit:
        net_bank = fees - collect
        return {
            "action": "COLLECT_BANK" if net_bank > 0 else "LEAVE",
            "reason": "TACTICAL_PROFIT_BANKING" if net_bank > 0 else "COLLECTION_COST_EXCEEDS_FEES",
            "economically_positive": net_bank > 0,
            "fee_to_collect_cost": round(ratio, 3),
            "net_value_after_cost": round(net_bank, 6),
        }

    incremental_return = fees * max(0.0, float(expected_apr_pct or 0.0)) / 100.0 * max(0.0, float(horizon_days or 0.0)) / 365.0
    net_compound_benefit = incremental_return - total_compound_cost
    if net_compound_benefit > 0 and (total_compound_cost <= 0 or fees / total_compound_cost >= min_fee_to_cost_multiple):
        return {
            "action": "COLLECT_COMPOUND",
            "reason": "EXPECTED_INCREMENTAL_FEES_EXCEED_LIFECYCLE_COST",
            "economically_positive": True,
            "fee_to_collect_cost": round(ratio, 3),
            "expected_incremental_return": round(incremental_return, 6),
            "execution_cost": round(total_compound_cost, 6),
            "net_compound_benefit": round(net_compound_benefit, 6),
        }

    net_bank = fees - collect
    return {
        "action": "COLLECT_BANK" if net_bank > 0 and sleeve == CORE_INCOME else "LEAVE",
        "reason": "COMPOUNDING_NOT_YET_ECONOMIC" if net_bank > 0 else "COLLECTION_NOT_ECONOMIC",
        "economically_positive": net_bank > 0,
        "fee_to_collect_cost": round(ratio, 3),
        "expected_incremental_return": round(incremental_return, 6),
        "execution_cost": round(total_compound_cost, 6),
        "net_compound_benefit": round(net_compound_benefit, 6),
    }
