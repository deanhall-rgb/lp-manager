from __future__ import annotations

import math
import time
import uuid
from typing import Any

from .analytics import range_metrics
from .event_engine import detect_material_event, review_due
from .outcome_audit import audit_decision, audit_range_outcome
from .range_lab import candle_activity_fraction, rank_range_candidates
from .strategy import deterministic_plan, edge_risk
from .economics_engine import estimate_replay_economics
from .market_regime import analyse_regime
from .targets import performance_targets
from .asset_lens import pool_price_lens


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(candles):
        close = _f(c.get("close") or c.get("current_price"))
        if close <= 0:
            continue
        ts = _f(c.get("timestamp"), float(i))
        low = _f(c.get("low"), close)
        high = _f(c.get("high"), close)
        rows.append({
            **c,
            "timestamp": ts,
            "open": _f(c.get("open"), close),
            "high": max(high, low, close),
            "low": min(low, high, close),
            "close": close,
            "volume": max(0.0, _f(c.get("volume"))),
        })
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def demo_replay_candles(name: str = "CORE_TREND", *, points: int = 240) -> list[dict[str, Any]]:
    name = str(name).upper()
    rows: list[dict[str, Any]] = []
    start = 1_780_000_000.0
    if name == "TACTICAL_BREAKOUT":
        base = 15.0
        for i in range(points):
            trend = i * 0.006
            wave = math.sin(i / 5.0) * 0.38
            breakout = max(0, i - int(points * 0.55)) * 0.035
            retrace = max(0, i - int(points * 0.78)) * -0.022
            close = max(1.0, base + trend + wave + breakout + retrace)
            span = 0.18 + abs(math.sin(i / 3.0)) * 0.18
            rows.append({"timestamp": start + i * 3600, "open": close - 0.03, "high": close + span, "low": max(0.1, close - span), "close": close, "volume": 900_000 + 500_000 * (1 + math.sin(i / 7.0))})
    else:
        base = 4100.0
        for i in range(points):
            trend = i * 1.55
            wave = math.sin(i / 10.0) * 75.0
            shock = -180.0 * math.exp(-((i - points * 0.62) / 12.0) ** 2)
            close = base + trend + wave + shock
            span = 30.0 + abs(math.sin(i / 4.0)) * 25.0
            rows.append({"timestamp": start + i * 3600, "open": close - 5.0, "high": close + span, "low": close - span, "close": close, "volume": 45_000_000 + 12_000_000 * (1 + math.sin(i / 15.0))})
    return rows


def run_replay(
    candles: list[dict[str, Any]],
    *,
    sleeve: str = "CORE_INCOME",
    pair: str = "WETH/USDC",
    chain: str = "BASE",
    protocol: str = "UNISWAP_V3",
    warmup_candles: int = 72,
    lookback_candles: int | None = None,
    initial_capital: float = 1000.0,
    inventory_intent: str = "BALANCED",
    future_audit_candles: int = 24,
    pool_context: dict[str, Any] | None = None,
    target_monthly_pct: float = 10.0,
) -> dict[str, Any]:
    rows = normalize_candles(candles)
    if len(rows) <= warmup_candles + 2:
        raise ValueError("not enough candles for warmup + replay")
    lookback = max(12, int(lookback_candles or warmup_candles))
    history = rows[max(0, warmup_candles - lookback):warmup_candles]
    # No-lookahead invariant: range selection can only see candles strictly
    # before the first replay candle.
    spot = history[-1]["close"]
    ranked = rank_range_candidates(history, spot, sleeve=sleeve)
    chosen = ranked[0]
    lower = float(chosen["candidate"]["lower"])
    upper = float(chosen["candidate"]["upper"])

    timeline: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    last_strategy_review_at: float | None = None
    last_ai_review_at: float | None = None
    strategy_reviews = 0
    ai_wakes = 0
    material_events = 0
    activities: list[float] = []

    for idx in range(warmup_candles, len(rows)):
        c = rows[idx]
        position = {
            "id": "replay-position",
            "pair": pair,
            "chain": chain,
            "protocol": protocol,
            "strategy_sleeve": sleeve,
            "inventory_intent": inventory_intent,
            "lower_price": lower,
            "upper_price": upper,
            "current_price": c["close"],
            "capital_value": initial_capital,
            "current_value": initial_capital,
            "apr_current": _f(c.get("apr_current"), 0.0),
        }
        metrics = range_metrics(position)
        risk = edge_risk(position)
        snapshot = {
            "timestamp": c["timestamp"],
            "current_price": c["close"],
            "apr_current": _f(c.get("apr_current"), 0.0),
            "liquidity_usd": _f(c.get("liquidity_usd"), 0.0),
            "sentiment_score": c.get("sentiment_score"),
            **metrics,
        }
        event = detect_material_event(previous, snapshot, strategy_sleeve=sleeve)
        due = review_due(
            strategy_sleeve=sleeve,
            last_strategy_review_at=last_strategy_review_at,
            last_ai_review_at=last_ai_review_at,
            now=c["timestamp"],
        )
        should_review = event["wake_strategy"] or due["strategy_due"]
        decision_row = None
        if event["material"]:
            material_events += 1
        if should_review:
            d = deterministic_plan(position).to_dict()
            decision_row = {
                "timestamp": c["timestamp"],
                "index": idx,
                "action": d["action"],
                "severity": d["severity"],
                "confidence": d["confidence"],
                "summary": d["summary"],
                "trigger": d["trigger"],
                "risk_score": risk["score"],
                "range_state": metrics["range_state"],
                "event_reasons": event["reasons"],
                "routine_due": due["strategy_due"],
            }
            future = rows[idx + 1:idx + 1 + future_audit_candles]
            outcome = audit_range_outcome(lower=lower, upper=upper, entry_price=c["close"], future_rows=future) if future else {"samples": 0, "classification": "NO_FUTURE_DATA"}
            decision_row["audit"] = audit_decision(d, outcome)
            decisions.append(decision_row)
            strategy_reviews += 1
            last_strategy_review_at = c["timestamp"]
        if event["wake_ai"] or due["ai_due"]:
            ai_wakes += 1
            last_ai_review_at = c["timestamp"]
        activity = candle_activity_fraction(c, lower, upper)
        activities.append(activity)
        if event["material"] or decision_row is not None or metrics["range_state"] != (previous or {}).get("range_state"):
            timeline.append({
                "timestamp": c["timestamp"],
                "index": idx,
                "price": c["close"],
                "range_state": metrics["range_state"],
                "nearest_edge_pct": metrics["nearest_edge_pct"],
                "risk_score": risk["score"],
                "risk_band": risk["band"],
                "event": event,
                "decision": decision_row,
            })
        previous = snapshot

    severity_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    for d in decisions:
        severity_counts[d["severity"]] = severity_counts.get(d["severity"], 0) + 1
        action_counts[d["action"]] = action_counts.get(d["action"], 0) + 1
        verdict = d["audit"]["verdict"]
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    observed_fee_values = [_f(r.get("position_fee_value"), 0.0) for r in rows[warmup_candles:]]
    has_fee_observations = any(v != 0.0 for v in observed_fee_values)
    replay_rows = rows[warmup_candles:]
    regime_at_entry = analyse_regime(history)
    if has_fee_observations:
        economics = {
            "mode": "OBSERVED_POSITION_FEE_SERIES",
            "estimated": False,
            "observed_fees": round(sum(observed_fee_values), 6),
            "note": "Fee values were supplied by the historical position series.",
        }
    elif pool_context:
        economics = estimate_replay_economics(
            replay_rows, activities, pool_context, capital=initial_capital,
            width_pct=float(chosen["candidate"].get("width_pct") or 25.0),
            regime=regime_at_entry, interventions=material_events,
        )
    else:
        economics = {
            "mode": "UNAVAILABLE_WITH_OHLC_ONLY",
            "estimated": True,
            "observed_fees": None,
            "note": "No pool TVL/fee context was supplied. Range behaviour is real; fee/P&L economics are unavailable for this replay.",
        }
    if economics.get("estimated_fees_day_usd") is not None:
        target_comparison = performance_targets(
            capital=initial_capital, target_monthly_pct=target_monthly_pct,
            actual_today=float(economics.get("estimated_fees_day_usd") or 0),
            actual_7d=float(economics.get("estimated_fees_day_usd") or 0)*7.0,
            actual_30d=max(0.0,float(economics.get("estimated_net_month_run_rate_usd") or 0)),
        )
    else:
        target_comparison = performance_targets(capital=initial_capital,target_monthly_pct=target_monthly_pct)

    result = {
        "id": uuid.uuid4().hex,
        "created_at": time.time(),
        "schema_version": "1.0",
        "mode": "DECISION_REPLAY",
        "no_lookahead": True,
        "sleeve": str(sleeve).upper(),
        "pair": pair,
        "chain": chain,
        "protocol": protocol,
        "input": {"candles": len(rows), "warmup_candles": warmup_candles, "lookback_candles": len(history)},
        "range_selection": {"spot_at_selection": spot, "chosen": chosen, "top_candidates": ranked[:5]},
        "price_lens": pool_price_lens(pool_context or {}, lower=lower, upper=upper, current=spot) if pool_context else {"primary_display":"TOKEN_PRICE"},
        "regime_at_entry": regime_at_entry,
        "summary": {
            "replay_candles": len(activities),
            "active_time_pct": round(sum(activities) / len(activities) * 100.0, 3) if activities else 0.0,
            "strategy_reviews": strategy_reviews,
            "ai_wakes": ai_wakes,
            "material_events": material_events,
            "severity_counts": severity_counts,
            "action_counts": action_counts,
            "audit_verdict_counts": verdict_counts,
        },
        "economics": economics,
        "target_comparison": target_comparison,
        "decisions": decisions,
        "timeline": timeline,
    }
    return result
