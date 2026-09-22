from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from statistics import median
from typing import Any, Iterable


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class RangeCandidate:
    lower: float
    upper: float
    center: float
    width_pct: float
    skew_pct: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def infer_candles_per_day(candles: Iterable[dict[str, Any]], fallback: float = 24.0) -> float:
    stamps = sorted(_f(c.get("timestamp")) for c in candles if _f(c.get("timestamp")) > 0)
    gaps = [b - a for a, b in zip(stamps, stamps[1:]) if b > a]
    if not gaps:
        return fallback
    step = median(gaps)
    return 86400.0 / step if step > 0 else fallback


def candle_activity_fraction(candle: dict[str, Any], lower: float, upper: float) -> float:
    """Approximate how much of a candle's travelled price interval overlaps a range.

    This is deliberately deterministic and conservative. It is not tick-level
    reconstruction and should be replaced/augmented by finer data where available.
    """
    low = _f(candle.get("low"), _f(candle.get("close")))
    high = _f(candle.get("high"), _f(candle.get("close")))
    close = _f(candle.get("close"))
    if lower <= close <= upper and high <= upper and low >= lower:
        return 1.0
    if high < lower or low > upper:
        return 0.0
    span = max(high - low, max(abs(close), 1.0) * 1e-9)
    overlap = max(0.0, min(high, upper) - max(low, lower))
    # If a zero-range candle closes in range, count it as fully active.
    if high == low:
        return 1.0 if lower <= close <= upper else 0.0
    return _clamp(overlap / span)


def analyse_range(
    candles: list[dict[str, Any]],
    lower: float,
    upper: float,
    *,
    horizon_days: float = 30.0,
    candles_per_day: float | None = None,
) -> dict[str, Any]:
    if lower <= 0 or upper <= lower:
        raise ValueError("invalid range")
    rows = [c for c in candles if _f(c.get("close")) > 0]
    if not rows:
        raise ValueError("no usable candles")
    cpd = candles_per_day or infer_candles_per_day(rows)
    activities = [candle_activity_fraction(c, lower, upper) for c in rows]
    total_volume = sum(max(0.0, _f(c.get("volume"))) for c in rows)
    captured_volume = sum(max(0.0, _f(c.get("volume"))) * a for c, a in zip(rows, activities))
    active_pct = sum(activities) / len(activities) * 100.0
    volume_capture_pct = (captured_volume / total_volume * 100.0) if total_volume > 0 else active_pct

    states: list[str] = []
    for c in rows:
        close = _f(c.get("close"))
        states.append("BELOW" if close < lower else "ABOVE" if close > upper else "IN")

    excursions = 0
    reentries = 0
    prev = states[0]
    longest_oor = 0
    current_oor = 0
    below_closes = 0
    above_closes = 0
    for state in states:
        if state == "IN":
            if prev != "IN":
                reentries += 1
            current_oor = 0
        else:
            if prev == "IN":
                excursions += 1
            current_oor += 1
            longest_oor = max(longest_oor, current_oor)
            below_closes += state == "BELOW"
            above_closes += state == "ABOVE"
        prev = state

    horizon_n = max(1, int(round(horizon_days * cpd)))
    strict_windows = 0
    window_count = 0
    mean_window_activity: list[float] = []
    if len(rows) >= horizon_n:
        for start in range(0, len(rows) - horizon_n + 1):
            window = activities[start:start + horizon_n]
            window_count += 1
            mean_window_activity.append(sum(window) / len(window))
            if all(states[i] == "IN" for i in range(start, start + horizon_n)):
                strict_windows += 1
    else:
        mean_window_activity = [sum(activities) / len(activities)]
        window_count = 1
        strict_windows = int(all(s == "IN" for s in states))

    strict_survival_pct = strict_windows / window_count * 100.0 if window_count else 0.0
    average_horizon_activity_pct = sum(mean_window_activity) / len(mean_window_activity) * 100.0
    reentry_rate_pct = reentries / excursions * 100.0 if excursions else 100.0
    longest_oor_hours = longest_oor / max(cpd, 1e-9) * 24.0

    durability_score = (
        active_pct * 0.30
        + volume_capture_pct * 0.25
        + average_horizon_activity_pct * 0.20
        + strict_survival_pct * 0.10
        + reentry_rate_pct * 0.10
        + max(0.0, 100.0 - min(100.0, longest_oor_hours / max(horizon_days * 24.0, 1.0) * 100.0)) * 0.05
    )

    return {
        "lower": lower,
        "upper": upper,
        "samples": len(rows),
        "candles_per_day": round(cpd, 4),
        "horizon_days": horizon_days,
        "active_time_pct": round(active_pct, 3),
        "volume_capture_pct": round(volume_capture_pct, 3),
        "strict_horizon_survival_pct": round(strict_survival_pct, 3),
        "average_horizon_activity_pct": round(average_horizon_activity_pct, 3),
        "excursions": excursions,
        "reentries": reentries,
        "reentry_rate_pct": round(reentry_rate_pct, 3),
        "longest_out_of_range_hours": round(longest_oor_hours, 3),
        "below_close_pct": round(below_closes / len(states) * 100.0, 3),
        "above_close_pct": round(above_closes / len(states) * 100.0, 3),
        "durability_score": round(max(0.0, min(100.0, durability_score)), 3),
        "method": "OHLC_INTERVAL_OVERLAP_APPROXIMATION",
    }


def generate_range_candidates(
    spot: float,
    *,
    half_widths_pct: Iterable[float] = (8, 10, 12.5, 15, 20, 25, 30, 40),
    skews_pct: Iterable[float] = (-10, -5, 0, 5, 10),
) -> list[RangeCandidate]:
    if spot <= 0:
        raise ValueError("spot must be positive")
    out: list[RangeCandidate] = []
    seen: set[tuple[float, float]] = set()
    for half in half_widths_pct:
        half = float(half)
        if half <= 0:
            continue
        for skew in skews_pct:
            skew = float(skew)
            center = spot * (1.0 + skew / 100.0)
            lower = max(spot * 1e-6, center * (1.0 - half / 100.0))
            upper = center * (1.0 + half / 100.0)
            key = (round(lower, 12), round(upper, 12))
            if key in seen or not lower < upper:
                continue
            seen.add(key)
            out.append(RangeCandidate(lower, upper, center, (upper / lower - 1.0) * 100.0, skew))
    return out


def rank_range_candidates(
    candles: list[dict[str, Any]],
    spot: float,
    *,
    sleeve: str = "CORE_INCOME",
    half_widths_pct: Iterable[float] | None = None,
    skews_pct: Iterable[float] | None = None,
    horizon_days: float | None = None,
) -> list[dict[str, Any]]:
    sleeve = str(sleeve or "CORE_INCOME").upper()
    if half_widths_pct is None:
        half_widths_pct = (10, 12.5, 15, 20, 25, 30, 40) if sleeve == "CORE_INCOME" else (4, 6, 8, 10, 12.5, 15, 20)
    if skews_pct is None:
        skews_pct = (-10, -5, 0, 5, 10) if sleeve == "CORE_INCOME" else (-6, -3, 0, 3, 6)
    if horizon_days is None:
        horizon_days = 30.0 if sleeve == "CORE_INCOME" else 3.0

    rows: list[dict[str, Any]] = []
    for candidate in generate_range_candidates(spot, half_widths_pct=half_widths_pct, skews_pct=skews_pct):
        analysis = analyse_range(candles, candidate.lower, candidate.upper, horizon_days=horizon_days)
        # Core rewards durability much more strongly; tactical can accept lower
        # residence for tighter capital efficiency, but still cannot ignore it.
        width_penalty = min(25.0, max(0.0, candidate.width_pct - (70.0 if sleeve == "CORE_INCOME" else 30.0)) * 0.20)
        if sleeve == "CORE_INCOME":
            score = analysis["durability_score"] - width_penalty
        else:
            tightness_bonus = max(0.0, 25.0 - candidate.width_pct) * 0.35
            score = analysis["durability_score"] * 0.75 + tightness_bonus - width_penalty
        rows.append({"candidate": candidate.to_dict(), "analysis": analysis, "range_score": round(max(0.0, min(100.0, score)), 3)})
    rows.sort(key=lambda r: r["range_score"], reverse=True)
    return rows
