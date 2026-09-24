from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _pct(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0 if a > 0 else 0.0


def _window_return(closes: list[float], periods: int) -> float:
    if len(closes) < 2:
        return 0.0
    start = closes[max(0, len(closes) - 1 - periods)]
    return _pct(start, closes[-1])


def _ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (max(1, span) + 1.0)
    out = values[0]
    for value in values[1:]:
        out = alpha * value + (1.0 - alpha) * out
    return out


def analyse_regime(candles: list[dict[str, Any]], *, candles_per_day: int = 24) -> dict[str, Any]:
    rows = [c for c in candles if _f(c.get("close")) > 0]
    if len(rows) < 8:
        return {
            "label": "INSUFFICIENT_DATA", "score": 50.0, "direction": "NEUTRAL",
            "confidence": 20.0, "returns": {}, "realised_volatility_pct": 0.0,
            "volume_trend_pct": 0.0, "breakout_state": "UNKNOWN", "range_skew_pct": 0.0,
            "reasons": ["Not enough candles to classify the current regime."],
        }

    closes = [_f(r.get("close")) for r in rows]
    volumes = [max(0.0, _f(r.get("volume"))) for r in rows]
    cpd = max(1, int(candles_per_day))
    r1 = _window_return(closes, cpd)
    r7 = _window_return(closes, cpd * 7)
    r30 = _window_return(closes, cpd * 30)

    log_returns = []
    for a, b in zip(closes[-min(len(closes), cpd * 30):], closes[-min(len(closes), cpd * 30)+1:]):
        if a > 0 and b > 0:
            log_returns.append(math.log(b / a))
    vol_daily = pstdev(log_returns) * math.sqrt(cpd) * 100.0 if len(log_returns) > 1 else 0.0

    fast = _ema(closes[-min(len(closes), cpd * 14):], max(3, cpd * 2))
    slow = _ema(closes[-min(len(closes), cpd * 30):], max(5, cpd * 7))
    ema_gap = _pct(slow, fast) if slow > 0 else 0.0

    recent_window = closes[-min(len(closes), cpd * 7):]
    prior_window = closes[-min(len(closes), cpd * 30):-min(len(closes), cpd * 7)]
    prior_high = max(prior_window) if prior_window else max(recent_window)
    prior_low = min(prior_window) if prior_window else min(recent_window)
    spot = closes[-1]
    if prior_high > 0 and spot > prior_high * 1.015:
        breakout = "UPSIDE_BREAKOUT"
    elif prior_low > 0 and spot < prior_low * 0.985:
        breakout = "DOWNSIDE_BREAKDOWN"
    else:
        breakout = "INSIDE_PRIOR_RANGE"

    nvol = min(len(volumes), cpd * 14)
    if nvol >= max(4, cpd * 2):
        half = max(1, nvol // 2)
        older = mean(volumes[-nvol:-half]) if volumes[-nvol:-half] else 0.0
        newer = mean(volumes[-half:]) if volumes[-half:] else 0.0
        volume_trend = _pct(older, newer) if older > 0 else 0.0
    else:
        volume_trend = 0.0

    momentum = 0.40 * max(-25.0, min(25.0, r7)) + 0.25 * max(-25.0, min(25.0, r30)) + 0.25 * max(-15.0, min(15.0, r1)) + 0.10 * max(-15.0, min(15.0, ema_gap))
    if breakout == "UPSIDE_BREAKOUT":
        momentum += 7.0
    elif breakout == "DOWNSIDE_BREAKDOWN":
        momentum -= 7.0
    momentum = max(-35.0, min(35.0, momentum))
    score = max(0.0, min(100.0, 50.0 + momentum * 1.35))

    if score >= 67:
        direction = "BULLISH"
        label = "BULL_TREND" if breakout != "UPSIDE_BREAKOUT" else "BULL_BREAKOUT"
    elif score <= 33:
        direction = "BEARISH"
        label = "BEAR_TREND" if breakout != "DOWNSIDE_BREAKDOWN" else "BEAR_BREAKDOWN"
    else:
        direction = "NEUTRAL"
        label = "RANGE_OR_TRANSITION"

    # A range recommendation should move with the market rather than blindly
    # anchoring to the historical mean. Positive means more room above spot.
    range_skew = max(-30.0, min(30.0, (score - 50.0) * 0.50))
    confidence = min(95.0, 45.0 + min(35.0, len(rows) / max(1, cpd * 30) * 30.0) + min(15.0, abs(score - 50.0) * 0.4))

    reasons = [f"1d {r1:+.1f}%, 7d {r7:+.1f}%, 30d {r30:+.1f}%"]
    if breakout != "INSIDE_PRIOR_RANGE":
        reasons.append(breakout.replace("_", " ").title())
    if abs(volume_trend) >= 10:
        reasons.append(f"Recent volume {'up' if volume_trend > 0 else 'down'} {abs(volume_trend):.0f}% versus prior window")
    if vol_daily >= 5:
        reasons.append(f"Elevated realised daily volatility {vol_daily:.1f}%")

    return {
        "label": label,
        "score": round(score, 1),
        "direction": direction,
        "confidence": round(confidence, 1),
        "returns": {"1d_pct": round(r1, 2), "7d_pct": round(r7, 2), "30d_pct": round(r30, 2)},
        "ema_gap_pct": round(ema_gap, 2),
        "realised_volatility_pct": round(vol_daily, 2),
        "volume_trend_pct": round(volume_trend, 2),
        "breakout_state": breakout,
        "range_skew_pct": round(range_skew, 2),
        "reasons": reasons,
    }
