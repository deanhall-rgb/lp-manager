from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass(frozen=True)
class AllocationLimits:
    minimum_reserve_pct: float = 10.0
    max_tactical_total_pct: float = 20.0
    max_core_single_position_pct: float = 40.0
    max_tactical_single_position_pct: float = 7.5
    max_single_chain_pct: float = 60.0
    minimum_candidate_score: float = 65.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def portfolio_exposure(positions: list[dict[str, Any]]) -> dict[str, Any]:
    open_rows = [p for p in positions if str(p.get("status") or "").upper() == "OPEN"]
    total = sum(max(0.0, _f(p.get("current_value"))) for p in open_rows)
    sleeves: dict[str, float] = {}
    chains: dict[str, float] = {}
    pairs: dict[str, float] = {}
    for p in open_rows:
        value = max(0.0, _f(p.get("current_value")))
        sleeve = str(p.get("strategy_sleeve") or TACTICAL_CAMPAIGN).upper()
        chain = str(p.get("chain") or "UNKNOWN").upper()
        pair = str(p.get("pair") or "UNKNOWN").upper()
        sleeves[sleeve] = sleeves.get(sleeve, 0.0) + value
        chains[chain] = chains.get(chain, 0.0) + value
        pairs[pair] = pairs.get(pair, 0.0) + value
    return {"total_open_value": total, "by_sleeve": sleeves, "by_chain": chains, "by_pair": pairs}


def suggest_allocation(
    *,
    total_portfolio_value: float,
    available_cash: float,
    positions: list[dict[str, Any]],
    opportunity: dict[str, Any],
    limits: AllocationLimits | None = None,
) -> dict[str, Any]:
    """Suggest a bounded allocation without forcing capital deployment.

    Limits are ceilings, not allocation targets. A missing/weak opportunity gets
    zero even when sleeve capacity is available.
    """
    limits = limits or AllocationLimits()
    total = max(0.0, _f(total_portfolio_value))
    cash = max(0.0, _f(available_cash))
    exposure = portfolio_exposure(positions)
    evaluation = opportunity.get("evaluation") or opportunity
    sleeve = str(
        opportunity.get("preferred_sleeve")
        or evaluation.get("preferred_sleeve")
        or ""
    ).upper()
    score = _f(opportunity.get("preferred_score"), _f(evaluation.get("preferred_score")))
    chain = str((opportunity.get("candidate") or {}).get("chain") or opportunity.get("chain") or "UNKNOWN").upper()
    blockers: list[str] = []

    if sleeve not in {CORE_INCOME, TACTICAL_CAMPAIGN}:
        blockers.append("NO_ELIGIBLE_SLEEVE")
    if score < limits.minimum_candidate_score:
        blockers.append("CANDIDATE_SCORE_BELOW_ALLOCATION_THRESHOLD")
    if total <= 0:
        blockers.append("PORTFOLIO_VALUE_MISSING")
    if cash <= 0:
        blockers.append("NO_AVAILABLE_CASH")

    reserve_value = total * limits.minimum_reserve_pct / 100.0
    # available_cash is assumed to be part of total_portfolio_value. Retain the
    # reserve even if every strategy bucket has free capacity.
    deployable_cash = max(0.0, cash - reserve_value)
    if deployable_cash <= 0:
        blockers.append("RESERVE_FLOOR_WOULD_BE_BREACHED")

    existing_sleeve = exposure["by_sleeve"].get(sleeve, 0.0)
    if sleeve == TACTICAL_CAMPAIGN:
        sleeve_cap = total * limits.max_tactical_total_pct / 100.0
        sleeve_room = max(0.0, sleeve_cap - existing_sleeve)
        single_cap = total * limits.max_tactical_single_position_pct / 100.0
    else:
        sleeve_cap = None
        sleeve_room = total
        single_cap = total * limits.max_core_single_position_pct / 100.0

    chain_cap = total * limits.max_single_chain_pct / 100.0
    chain_room = max(0.0, chain_cap - exposure["by_chain"].get(chain, 0.0))

    # Scale only within safety ceilings: higher-quality candidates can use more
    # of the single-position ceiling, but capital is never required to fill it.
    quality_fraction = max(0.0, min(1.0, (score - limits.minimum_candidate_score) / max(1.0, 100.0 - limits.minimum_candidate_score)))
    quality_fraction = 0.25 + 0.75 * quality_fraction if score >= limits.minimum_candidate_score else 0.0
    quality_cap = single_cap * quality_fraction

    suggested = min(deployable_cash, sleeve_room, single_cap, chain_room, quality_cap)
    if blockers:
        suggested = 0.0
    if sleeve == TACTICAL_CAMPAIGN and sleeve_room <= 0:
        blockers.append("TACTICAL_BOOK_AT_CAP")
        suggested = 0.0
    if chain_room <= 0:
        blockers.append("CHAIN_CONCENTRATION_AT_CAP")
        suggested = 0.0

    return {
        "eligible": suggested > 0 and not blockers,
        "suggested_capital": round(max(0.0, suggested), 2),
        "sleeve": sleeve or None,
        "candidate_score": round(score, 2),
        "blockers": sorted(set(blockers)),
        "limits": limits.to_dict(),
        "capacity": {
            "available_cash": round(cash, 2),
            "reserve_floor": round(reserve_value, 2),
            "deployable_cash": round(deployable_cash, 2),
            "existing_sleeve_exposure": round(existing_sleeve, 2),
            "sleeve_room": round(sleeve_room, 2),
            "single_position_ceiling": round(single_cap, 2),
            "chain_room": round(chain_room, 2),
            "quality_adjusted_ceiling": round(quality_cap, 2),
        },
        "note": "Allocation limits are safety ceilings, not targets; unused capacity may remain in reserve.",
    }
