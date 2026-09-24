from __future__ import annotations
from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _economics(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("economics") or row.get("quick_economics") or {}


def _reject_reasons(row: dict[str, Any], *, score: float, sleeve: str) -> list[str]:
    economics = _economics(row)
    evaluation = row.get("evaluation") or {}
    risk = (evaluation.get("risk_core") if sleeve == "CORE_INCOME" else evaluation.get("risk_tactical")) or {}
    reasons: list[str] = []
    threshold = 54.0 if sleeve == "CORE_INCOME" else 57.0
    operating = _f(economics.get("estimated_operating_net_month_usd"), _f(economics.get("estimated_net_month_usd")))
    quality = economics.get("volume_quality") or {}
    if not risk.get("eligible", True):
        blockers = risk.get("blockers") or []
        reasons.append("risk gate" + (f": {', '.join(map(str, blockers[:2]))}" if blockers else ""))
    if operating <= 0:
        reasons.append("fee operating return does not currently clear zero")
    if _f(quality.get("factor"), 1.0) < 0.20:
        reasons.append("current activity is too anomalous to extrapolate")
    if score < threshold:
        reasons.append(f"score {score:.0f} below {threshold:.0f} {sleeve.lower().replace('_',' ')} threshold")
    return reasons


def rank_opportunities(
    rows: list[dict[str, Any]], *, available_capital: float, reserve_pct: float = 10.0,
    max_positions: int = 4, sleeve_filter: str = "ANY", allocation_mode: str = "DIVERSIFIED",
) -> dict[str, Any]:
    capital = max(0.0, _f(available_capital))
    reserve_floor = capital * max(0.0, min(90.0, _f(reserve_pct))) / 100.0
    deployable = max(0.0, capital - reserve_floor)
    sleeve_filter = str(sleeve_filter or "ANY").upper()
    allocation_mode = str(allocation_mode or "DIVERSIFIED").upper()
    scored: list[dict[str, Any]] = []

    for row in rows:
        economics = _economics(row)
        evaluation = row.get("evaluation") or {}
        regime = row.get("regime") or {}
        sleeve = str(row.get("sleeve") or evaluation.get("preferred_sleeve") or "").upper()
        if not sleeve:
            sleeve = "CORE_INCOME" if _f(evaluation.get("core_pre_score")) >= _f(evaluation.get("tactical_pre_score")) else "TACTICAL_CAMPAIGN"
        if sleeve_filter not in {"ANY", "ALL", ""} and sleeve != sleeve_filter:
            continue

        pre = _f(evaluation.get("core_pre_score") if sleeve == "CORE_INCOME" else evaluation.get("tactical_pre_score"))
        operating_pct = _f(economics.get("estimated_net_month_pct"))
        operating_month = _f(economics.get("estimated_operating_net_month_usd"), _f(economics.get("estimated_net_month_usd")))
        regime_conf = _f(regime.get("confidence"), 50.0)
        risk = (evaluation.get("risk_core") if sleeve == "CORE_INCOME" else evaluation.get("risk_tactical")) or {}
        risk_penalty = (30.0 if not risk.get("eligible", True) else 0.0) + min(20.0, len(risk.get("blockers") or []) * 5.0)
        quality_factor = _f((economics.get("volume_quality") or {}).get("factor"), 1.0)
        quality_penalty = 25.0 if quality_factor < 0.20 else 10.0 if quality_factor < 0.35 else 0.0
        # Profit is a material driver, but a single hot day cannot buy its way past risk controls.
        profit_score = max(0.0, min(100.0, 50.0 + operating_pct * 3.0)) if economics.get("mode") != "INSUFFICIENT_DATA" else 25.0
        score = max(0.0, min(100.0, 0.46 * pre + 0.34 * profit_score + 0.20 * regime_conf - risk_penalty - quality_penalty))
        reject = _reject_reasons(row, score=score, sleeve=sleeve)
        scored.append({**row, "sleeve": sleeve, "portfolio_score": round(score, 1), "reject_reasons": reject, "operating_net_month": round(operating_month, 2)})

    scored.sort(key=lambda x: (len(x.get("reject_reasons") or []), -x["portfolio_score"], -_f(x.get("operating_net_month"))))
    eligible = [r for r in scored if not r.get("reject_reasons")]
    if allocation_mode == "BEST_ONLY":
        eligible = eligible[:1]
    else:
        eligible = eligible[:max(1, int(max_positions))]

    near_misses = [
        {"chain": r.get("chain"), "pair": r.get("pair"), "pool_address": r.get("pool_address"), "sleeve": r.get("sleeve"),
         "score": r.get("portfolio_score"), "operating_net_month": r.get("operating_net_month"), "reject_reasons": r.get("reject_reasons") or []}
        for r in scored[:5] if r.get("reject_reasons")
    ][:3]

    if not eligible or deployable <= 0:
        return {
            "available_capital": capital, "reserve_floor": reserve_floor, "reserve": capital,
            "deployable": deployable, "allocated": 0, "allocations": [], "ranked": scored,
            "near_misses": near_misses,
            "reason": "No opportunity currently clears both the profit and risk gates. The closest candidates are shown for Strategy Lab investigation rather than being silently discarded.",
            "sleeve_filter": sleeve_filter, "allocation_mode": allocation_mode,
        }

    weights = [max(0.0, (r["portfolio_score"] - 45.0)) ** 1.5 for r in eligible]
    total = sum(weights) or 1.0
    caps = []
    for r in eligible:
        sleeve = str(r.get("sleeve") or "")
        cap_pct = 1.0 if (allocation_mode == "BEST_ONLY" and sleeve == "CORE_INCOME") else 0.35 if allocation_mode == "BEST_ONLY" else 0.75 if sleeve == "CORE_INCOME" else 0.30
        caps.append(deployable * cap_pct)
    amounts = [min(deployable * w / total, cap) for w, cap in zip(weights, caps)]

    tactical_limit = deployable * 0.35
    for _ in range(8):
        leftover = deployable - sum(amounts)
        if leftover < 0.01:
            break
        candidates = []
        tactical_used = sum(a for a, r in zip(amounts, eligible) if str(r.get("sleeve")) == "TACTICAL_CAMPAIGN")
        for i, (a, cap, r) in enumerate(zip(amounts, caps, eligible)):
            room = max(0.0, cap - a)
            if str(r.get("sleeve")) == "TACTICAL_CAMPAIGN":
                room = min(room, max(0.0, tactical_limit - tactical_used))
            if room > 0.01:
                candidates.append((i, room, max(1.0, weights[i])))
        if not candidates:
            break
        tw = sum(x[2] for x in candidates)
        for i, room, w in candidates:
            amounts[i] += min(room, leftover * w / tw)

    allocations = []
    for row, amount in zip(eligible, amounts):
        economics = _economics(row)
        basis = max(1.0, _f(economics.get("capital_usd"), 1000.0))
        operating_basis = _f(economics.get("estimated_operating_net_month_usd"), _f(economics.get("estimated_net_month_usd")))
        monthly = operating_basis * amount / basis
        allocations.append({
            "rank": len(allocations) + 1, "chain": row.get("chain"), "pair": row.get("pair"), "pool_address": row.get("pool_address"),
            "sleeve": row.get("sleeve"), "score": row["portfolio_score"], "amount": round(amount, 2),
            "expected_net_month": round(monthly, 2), "expected_net_month_pct": round(monthly / max(amount, 1e-9) * 100, 2),
            "economics_confidence": economics.get("confidence"), "economics_mode": economics.get("mode"), "why": row.get("why") or [],
        })
    allocated = sum(x["amount"] for x in allocations)
    unallocated = max(0.0, deployable - allocated)
    return {
        "available_capital": round(capital, 2), "reserve_floor": round(reserve_floor, 2), "unallocated": round(unallocated, 2),
        "reserve": round(reserve_floor + unallocated, 2), "deployable": round(deployable, 2), "allocated": round(allocated, 2),
        "allocations": allocations, "ranked": scored, "near_misses": near_misses, "sleeve_filter": sleeve_filter, "allocation_mode": allocation_mode,
        "unallocated_reason": "No eligible candidate has remaining concentration capacity." if unallocated > 0.01 else None,
        "guardrail": "PROFIT_FIRST_WITH_RISK_GATES: positive fee operating return is required; anomalous activity, token/protocol risk and sleeve concentration can still block deployment.",
    }
