from __future__ import annotations

import time
from typing import Any

from .portfolio_policy import policy_for


def review_due(
    *,
    strategy_sleeve: str,
    last_strategy_review_at: float | None,
    last_ai_review_at: float | None,
    now: float | None = None,
) -> dict[str, Any]:
    now = float(now or time.time())
    policy = policy_for(strategy_sleeve)
    last_strategy = float(last_strategy_review_at or 0.0)
    last_ai = float(last_ai_review_at or 0.0)
    return {
        "strategy_due": last_strategy <= 0 or now - last_strategy >= policy.strategy_review_seconds,
        "ai_due": last_ai <= 0 or now - last_ai >= policy.ai_review_seconds,
        "strategy_next_in_seconds": 0.0 if last_strategy <= 0 else max(0.0, policy.strategy_review_seconds - (now - last_strategy)),
        "ai_next_in_seconds": 0.0 if last_ai <= 0 else max(0.0, policy.ai_review_seconds - (now - last_ai)),
        "routine_strategy_seconds": policy.strategy_review_seconds,
        "routine_ai_seconds": policy.ai_review_seconds,
    }


def detect_material_event(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    strategy_sleeve: str,
) -> dict[str, Any]:
    previous = previous or {}
    policy = policy_for(strategy_sleeve)
    reasons: list[str] = []
    ai_reasons: list[str] = []

    prev_price = float(previous.get("current_price") or 0.0)
    price = float(current.get("current_price") or 0.0)
    if prev_price > 0 and price > 0:
        move = (price / prev_price - 1.0) * 100.0
        threshold = 4.0 if policy.name == "CORE_INCOME" else 2.0
        if abs(move) >= threshold:
            reasons.append(f"PRICE_MOVE:{move:+.2f}%")
        if abs(move) >= threshold * 2:
            ai_reasons.append(f"LARGE_PRICE_MOVE:{move:+.2f}%")

    prev_range = str(previous.get("range_state") or "")
    range_state = str(current.get("range_state") or "")
    if prev_range and range_state and prev_range != range_state:
        reasons.append(f"RANGE_STATE:{prev_range}->{range_state}")
        ai_reasons.append("RANGE_STATE_CHANGED")

    nearest = current.get("nearest_edge_pct")
    previous_nearest = previous.get("nearest_edge_pct")
    if nearest is not None:
        nearest = float(nearest)
        prev_nearest = float(previous_nearest) if previous_nearest is not None else None
        # Edge proximity is a state threshold, not a repeating event. Wake only
        # when crossing into a tighter band; routine cadence owns subsequent reviews.
        crossed_intervene = nearest <= policy.intervene_edge_pct and (prev_nearest is None or prev_nearest > policy.intervene_edge_pct)
        crossed_watch = (
            nearest <= policy.watch_edge_pct
            and nearest > policy.intervene_edge_pct
            and (prev_nearest is None or prev_nearest > policy.watch_edge_pct)
        )
        if crossed_intervene:
            reasons.append(f"EDGE_INTERVENE:{nearest:.2f}%")
            ai_reasons.append("EDGE_INTERVENTION_THRESHOLD")
        elif crossed_watch:
            reasons.append(f"EDGE_WATCH:{nearest:.2f}%")

    prev_apr = float(previous.get("apr_current") or 0.0)
    apr = float(current.get("apr_current") or 0.0)
    if prev_apr > 0 and apr >= 0 and apr < prev_apr * 0.60:
        reasons.append("APR_DROPPED_40PCT_PLUS")

    prev_liq = float(previous.get("liquidity_usd") or 0.0)
    liq = float(current.get("liquidity_usd") or 0.0)
    if prev_liq > 0 and liq > 0 and liq < prev_liq * 0.75:
        reasons.append("LIQUIDITY_DROPPED_25PCT_PLUS")
        ai_reasons.append("MATERIAL_LIQUIDITY_CHANGE")

    prev_sent = previous.get("sentiment_score")
    sent = current.get("sentiment_score")
    if prev_sent is not None and sent is not None:
        prev_sent = float(prev_sent)
        sent = float(sent)
        if prev_sent >= 0.10 and sent <= -0.10:
            reasons.append("SENTIMENT_BULLISH_TO_BEARISH")
            ai_reasons.append("SENTIMENT_REGIME_REVERSAL")

    return {
        "material": bool(reasons),
        "wake_strategy": bool(reasons),
        "wake_ai": bool(ai_reasons),
        "reasons": reasons,
        "ai_reasons": ai_reasons,
    }
