from __future__ import annotations
from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _economics(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("economics") or row.get("quick_economics") or {}


def _pair_key(pair: str) -> str:
    parts = [x.strip().upper() for x in str(pair or "").replace("-", "/").split("/") if x.strip()]
    return "/".join(sorted(parts)) if len(parts) >= 2 else "/".join(parts)


def _pool_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("chain") or "").upper(),
        str(row.get("pool_address") or "").lower(),
    )


def _portfolio_exposure(open_positions: list[dict[str, Any]] | None, *, available_capital: float) -> dict[str, Any]:
    rows = [
        p for p in (open_positions or [])
        if str(p.get("status") or "OPEN").upper() == "OPEN"
        and str(p.get("monitoring_class") or "").upper() != "ARCHIVED_SUPERSEDED"
    ]
    by_pool: dict[tuple[str, str], float] = {}
    by_pair: dict[tuple[str, str], float] = {}
    by_chain: dict[str, float] = {}
    by_sleeve: dict[str, float] = {}
    total = 0.0
    for p in rows:
        value = max(0.0, _f(p.get("current_value"), _f(p.get("capital_value"))))
        if value <= 0:
            continue
        chain = str(p.get("chain") or "UNKNOWN").upper()
        address = str(p.get("pool_address") or "").lower()
        pair = _pair_key(str(p.get("pair") or ""))
        sleeve = str(p.get("strategy_sleeve") or "TACTICAL_CAMPAIGN").upper()
        total += value
        if address:
            by_pool[(chain, address)] = by_pool.get((chain, address), 0.0) + value
        if pair:
            by_pair[(chain, pair)] = by_pair.get((chain, pair), 0.0) + value
        by_chain[chain] = by_chain.get(chain, 0.0) + value
        by_sleeve[sleeve] = by_sleeve.get(sleeve, 0.0) + value
    basis = total + max(0.0, available_capital)
    return {
        "open_position_count": len(rows),
        "total_open_value": total,
        "portfolio_basis_after_available_capital": basis,
        "by_pool": by_pool,
        "by_pair": by_pair,
        "by_chain": by_chain,
        "by_sleeve": by_sleeve,
    }


def bounded_advisor_calibration(economics: dict[str, Any], calibration: dict[str, Any] | None) -> dict[str, Any]:
    """Apply only a small exact-pool evidence correction to Advisor economics.

    Current live economics remain the primary ranking input. Historical/owned fee
    evidence can only nudge the operating forecast by +/-10%, and only when the
    calibration contains mature exact-pool evidence. Pair-class history is kept as
    confidence context and cannot buy ranking points on its own.
    """
    base = dict(economics or {})
    calibration = calibration or {}
    raw_factor = _f(calibration.get("factor"), 1.0)
    confidence = str(calibration.get("confidence") or "UNAVAILABLE").upper()
    exact_samples = int(_f(calibration.get("exact_pool_samples"), 0))
    apply = exact_samples > 0 and confidence in {"MODERATE", "HIGH"}
    factor = max(0.90, min(1.10, raw_factor)) if apply else 1.0

    model_month = _f(base.get("estimated_operating_net_month_usd"), _f(base.get("estimated_net_month_usd")))
    model_pct = _f(base.get("estimated_net_month_pct"))
    adjusted_month = model_month * factor
    adjusted_pct = model_pct * factor
    base["advisor_live_model_operating_net_month_usd"] = round(model_month, 2)
    base["advisor_live_model_net_month_pct"] = round(model_pct, 2)
    base["estimated_operating_net_month_usd"] = round(adjusted_month, 2)
    base["estimated_net_month_usd"] = round(adjusted_month, 2)
    base["estimated_net_month_pct"] = round(adjusted_pct, 2)
    base["advisor_calibration"] = {
        "applied": bool(apply),
        "factor": round(factor, 4),
        "raw_factor": round(raw_factor, 4),
        "confidence": confidence,
        "exact_pool_samples": exact_samples,
        "sample_count": int(_f(calibration.get("sample_count"), 0)),
        "evidence_fraction": round(_f(calibration.get("evidence_fraction")), 4),
        "guardrail": "EXACT_POOL_ONLY_MAX_PLUS_MINUS_10_PERCENT",
        "note": "Current live economics drive ranking; owned/history evidence only provides a bounded exact-pool calibration.",
    }
    return base


def _reject_reasons(row: dict[str, Any], *, score: float, sleeve: str) -> list[str]:
    economics = _economics(row)
    evaluation = row.get("evaluation") or {}
    risk = (evaluation.get("risk_core") if sleeve == "CORE_INCOME" else evaluation.get("risk_tactical")) or {}
    reasons: list[str] = []
    threshold = 54.0 if sleeve == "CORE_INCOME" else 57.0
    operating = _f(economics.get("estimated_operating_net_month_usd"), _f(economics.get("estimated_net_month_usd")))
    quality = economics.get("volume_quality") or {}
    evidence_status = str(row.get("market_evidence_status") or "").upper()
    if evidence_status in {"PERSISTED_CACHE", "STALE_PERSISTED", "NOT_REFRESHED"}:
        reasons.append("current live market evidence was not refreshed in this advisor run")
    if not risk.get("eligible", True):
        blockers = risk.get("blockers") or []
        reasons.append("risk gate" + (f": {', '.join(map(str, blockers[:2]))}" if blockers else ""))
    if operating <= 0:
        reasons.append("fee operating return does not currently clear zero")
    if _f(quality.get("factor"), 1.0) < 0.20:
        reasons.append("current activity is too anomalous to extrapolate")
    readiness=row.get("profit_lab_readiness") or {}
    if str(readiness.get("status") or "").upper()=="HISTORY_FAILED":
        reasons.append("Profit Lab history failed for this pool")
    deep=row.get("deep_analysis") or {}
    deep_status=str(deep.get("status") or "NOT_ANALYSED").upper()
    if deep_status=="NOT_ANALYSED":
        reasons.append("deep Profit Lab validation required before allocation")
    elif deep_status=="DEEP_STALE":
        reasons.append("deep Profit Lab validation is stale")
    elif deep_status=="DEEP_NON_POSITIVE":
        reasons.append("deep Profit Lab expected net is not positive")
    if score < threshold:
        reasons.append(f"score {score:.0f} below {threshold:.0f} {sleeve.lower().replace('_',' ')} threshold")
    return reasons


def rank_opportunities(
    rows: list[dict[str, Any]], *, available_capital: float, reserve_pct: float = 10.0,
    max_positions: int = 4, sleeve_filter: str = "ANY", allocation_mode: str = "DIVERSIFIED",
    open_positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    capital = max(0.0, _f(available_capital))
    reserve_floor = capital * max(0.0, min(90.0, _f(reserve_pct))) / 100.0
    deployable = max(0.0, capital - reserve_floor)
    sleeve_filter = str(sleeve_filter or "ANY").upper()
    allocation_mode = str(allocation_mode or "DIVERSIFIED").upper()
    exposure = _portfolio_exposure(open_positions, available_capital=capital)
    portfolio_basis = max(0.0, _f(exposure.get("portfolio_basis_after_available_capital")))
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
        base_score = max(0.0, min(100.0, 0.46 * pre + 0.34 * profit_score + 0.20 * regime_conf - risk_penalty - quality_penalty))

        chain, address = _pool_key(row)
        pair = _pair_key(str(row.get("pair") or ""))
        existing_pool = _f(exposure["by_pool"].get((chain, address), 0.0)) if address else 0.0
        existing_pair = _f(exposure["by_pair"].get((chain, pair), 0.0)) if pair else 0.0
        existing_chain = _f(exposure["by_chain"].get(chain, 0.0))
        existing_sleeve = _f(exposure["by_sleeve"].get(sleeve, 0.0))
        pool_pct = existing_pool / portfolio_basis * 100.0 if portfolio_basis > 0 else 0.0
        pair_pct = existing_pair / portfolio_basis * 100.0 if portfolio_basis > 0 else 0.0
        chain_pct = existing_chain / portfolio_basis * 100.0 if portfolio_basis > 0 else 0.0
        # Existing exposure should influence an incremental allocation without
        # drowning out current live economics. The penalty is deliberately small
        # and bounded; the allocation-cap stage below does the harder concentration work.
        pool_penalty = min(10.0, pool_pct * 0.20)
        related_pair_penalty = min(4.0, max(0.0, pair_pct - pool_pct) * 0.08)
        concentration_penalty = min(12.0, pool_penalty + related_pair_penalty)
        score = max(0.0, min(100.0, base_score - concentration_penalty))
        reject = _reject_reasons(row, score=score, sleeve=sleeve)
        preview_cap_pct = 1.0 if (allocation_mode == "BEST_ONLY" and sleeve == "CORE_INCOME") else 0.35 if allocation_mode == "BEST_ONLY" else 0.75 if sleeve == "CORE_INCOME" else 0.30
        preview_pool_room = max(0.0, portfolio_basis * preview_cap_pct - existing_pool) if portfolio_basis > 0 else deployable * preview_cap_pct
        if portfolio_basis > 0 and preview_pool_room <= 0.01:
            reject.append("existing pool concentration leaves no incremental allocation room")
        scored.append({
            **row,
            "sleeve": sleeve,
            "base_portfolio_score": round(base_score, 1),
            "concentration_penalty": round(concentration_penalty, 1),
            "portfolio_score": round(score, 1),
            "reject_reasons": reject,
            "operating_net_month": round(operating_month, 2),
            "existing_exposure": {
                "pool_value": round(existing_pool, 2),
                "pair_value": round(existing_pair, 2),
                "chain_value": round(existing_chain, 2),
                "sleeve_value": round(existing_sleeve, 2),
                "pool_pct_of_portfolio_basis": round(pool_pct, 2),
                "pair_pct_of_portfolio_basis": round(pair_pct, 2),
                "chain_pct_of_portfolio_basis": round(chain_pct, 2),
            },
        })

    scored.sort(key=lambda x: (len(x.get("reject_reasons") or []), -x["portfolio_score"], -_f(x.get("operating_net_month"))))
    eligible = [r for r in scored if not r.get("reject_reasons")]
    if allocation_mode == "BEST_ONLY":
        eligible = eligible[:1]
    else:
        eligible = eligible[:max(1, int(max_positions))]

    near_misses = [
        {"chain": r.get("chain"), "pair": r.get("pair"), "pool_address": r.get("pool_address"), "sleeve": r.get("sleeve"),
         "score": r.get("portfolio_score"), "operating_net_month": r.get("operating_net_month"), "reject_reasons": r.get("reject_reasons") or [],
         "market_evidence_status": r.get("market_evidence_status"), "profit_lab_readiness": r.get("profit_lab_readiness") or {},
         "deep_analysis": r.get("deep_analysis") or {}, "existing_exposure": r.get("existing_exposure") or {}}
        for r in scored[:8] if r.get("reject_reasons")
    ][:3]

    portfolio_context = {
        "open_position_count": int(exposure.get("open_position_count") or 0),
        "existing_open_value": round(_f(exposure.get("total_open_value")), 2),
        "available_capital": round(capital, 2),
        "portfolio_basis_after_available_capital": round(portfolio_basis, 2),
        "note": "Advisor amounts are incremental additions. Entered capital is a new-money budget; existing open LP exposure informs per-pool concentration checks but does not reduce that new-money budget.",
    }

    if not eligible or deployable <= 0:
        return {
            "available_capital": capital, "reserve_floor": reserve_floor, "reserve": capital,
            "deployable": deployable, "allocated": 0, "unallocated": deployable, "allocations": [], "ranked": scored,
            "near_misses": near_misses, "portfolio_context": portfolio_context,
            "reason": "No opportunity currently clears the screen, freshness, risk and deep Profit Lab validation gates. The closest candidates remain research-only until deep economics confirm positive expected net.",
            "sleeve_filter": sleeve_filter, "allocation_mode": allocation_mode,
        }

    weights = [max(0.0, (r["portfolio_score"] - 45.0)) ** 1.5 for r in eligible]
    total = sum(weights) or 1.0
    caps = []
    cap_details = []
    for r in eligible:
        sleeve = str(r.get("sleeve") or "")
        cap_pct = 1.0 if (allocation_mode == "BEST_ONLY" and sleeve == "CORE_INCOME") else 0.35 if allocation_mode == "BEST_ONLY" else 0.75 if sleeve == "CORE_INCOME" else 0.30
        incremental_cap = deployable * cap_pct
        existing_pool = _f((r.get("existing_exposure") or {}).get("pool_value"))
        post_pool_ceiling = portfolio_basis * cap_pct if portfolio_basis > 0 else incremental_cap
        pool_room = max(0.0, post_pool_ceiling - existing_pool)
        cap = min(incremental_cap, pool_room)
        caps.append(cap)
        cap_details.append({
            "cap_pct": round(cap_pct * 100.0, 2),
            "incremental_cap": round(incremental_cap, 2),
            "post_pool_ceiling": round(post_pool_ceiling, 2),
            "existing_pool_value": round(existing_pool, 2),
            "incremental_room_after_existing": round(pool_room, 2),
        })
    amounts = [min(deployable * w / total, cap) for w, cap in zip(weights, caps)]

    for _ in range(8):
        leftover = deployable - sum(amounts)
        if leftover < 0.01:
            break
        candidates = []
        for i, (a, cap, r) in enumerate(zip(amounts, caps, eligible)):
            room = max(0.0, cap - a)
            if room > 0.01:
                candidates.append((i, room, max(1.0, weights[i])))
        if not candidates:
            break
        tw = sum(x[2] for x in candidates)
        for i, room, w in candidates:
            amounts[i] += min(room, leftover * w / tw)

    by_pool_new: dict[tuple[str, str], float] = {}
    by_pair_new: dict[tuple[str, str], float] = {}
    by_chain_new: dict[str, float] = {}
    by_sleeve_new: dict[str, float] = {}
    for row, amount in zip(eligible, amounts):
        chain, address = _pool_key(row)
        pair = _pair_key(str(row.get("pair") or ""))
        if address:
            by_pool_new[(chain, address)] = by_pool_new.get((chain, address), 0.0) + amount
        if pair:
            by_pair_new[(chain, pair)] = by_pair_new.get((chain, pair), 0.0) + amount
        by_chain_new[chain] = by_chain_new.get(chain, 0.0) + amount
        sleeve = str(row.get("sleeve") or "")
        by_sleeve_new[sleeve] = by_sleeve_new.get(sleeve, 0.0) + amount

    allocations = []
    for row, amount, cap_detail in zip(eligible, amounts, cap_details):
        economics = _economics(row)
        basis = max(1.0, _f(economics.get("capital_usd"), 1000.0))
        operating_basis = _f(economics.get("estimated_operating_net_month_usd"), _f(economics.get("estimated_net_month_usd")))
        monthly = operating_basis * amount / basis
        chain, address = _pool_key(row)
        pair_key = _pair_key(str(row.get("pair") or ""))
        existing = row.get("existing_exposure") or {}
        post_pool = _f(existing.get("pool_value")) + by_pool_new.get((chain, address), 0.0)
        post_pair = _f(existing.get("pair_value")) + by_pair_new.get((chain, pair_key), 0.0)
        post_chain = _f(existing.get("chain_value")) + by_chain_new.get(chain, 0.0)
        sleeve = str(row.get("sleeve") or "")
        post_sleeve = _f(existing.get("sleeve_value")) + by_sleeve_new.get(sleeve, 0.0)
        allocations.append({
            "rank": len(allocations) + 1, "chain": row.get("chain"), "pair": row.get("pair"), "pool_address": row.get("pool_address"),
            "sleeve": row.get("sleeve"), "score": row["portfolio_score"], "base_score": row.get("base_portfolio_score"),
            "concentration_penalty": row.get("concentration_penalty"), "amount": round(amount, 2), "incremental_amount": round(amount, 2),
            "expected_net_month": round(monthly, 2), "expected_net_month_pct": round(monthly / max(amount, 1e-9) * 100, 2),
            "economics_confidence": economics.get("confidence"), "economics_mode": economics.get("mode"), "why": row.get("why") or [],
            "market_evidence_status": row.get("market_evidence_status"),
            "profit_lab_readiness": row.get("profit_lab_readiness") or {},
            "deep_analysis": row.get("deep_analysis") or {},
            "advisor_calibration": economics.get("advisor_calibration") or {},
            "existing_exposure": existing,
            "post_allocation": {
                "portfolio_basis": round(portfolio_basis, 2),
                "pool_value": round(post_pool, 2),
                "pool_pct": round(post_pool / portfolio_basis * 100.0, 2) if portfolio_basis > 0 else 0.0,
                "pair_value": round(post_pair, 2),
                "pair_pct": round(post_pair / portfolio_basis * 100.0, 2) if portfolio_basis > 0 else 0.0,
                "chain_value": round(post_chain, 2),
                "chain_pct": round(post_chain / portfolio_basis * 100.0, 2) if portfolio_basis > 0 else 0.0,
                "sleeve_value": round(post_sleeve, 2),
                "sleeve_pct": round(post_sleeve / portfolio_basis * 100.0, 2) if portfolio_basis > 0 else 0.0,
            },
            "concentration_cap": cap_detail,
        })
    allocated = sum(x["amount"] for x in allocations)
    unallocated = max(0.0, deployable - allocated)
    concentration_limited = any(_f(c.get("incremental_room_after_existing")) + 0.01 < _f(c.get("incremental_cap")) for c in cap_details)
    return {
        "available_capital": round(capital, 2), "reserve_floor": round(reserve_floor, 2), "unallocated": round(unallocated, 2),
        "reserve": round(reserve_floor + unallocated, 2), "deployable": round(deployable, 2), "allocated": round(allocated, 2),
        "allocations": allocations, "ranked": scored, "near_misses": near_misses, "sleeve_filter": sleeve_filter, "allocation_mode": allocation_mode,
        "portfolio_context": portfolio_context,
        "unallocated_reason": "Existing portfolio exposure leaves no eligible candidate with more concentration room." if unallocated > 0.01 and concentration_limited else ("No eligible candidate has remaining concentration capacity." if unallocated > 0.01 else None),
        "guardrail": "SCREEN_THEN_DEEP_VALIDATE: current live economics rank candidates; stale cache cannot allocate; deep Profit Lab economics must confirm positive expected net before allocation; existing open LP exposure constrains incremental concentration.",
    }
