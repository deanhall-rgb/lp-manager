from __future__ import annotations

import math
from typing import Any

from .analytics import range_metrics
from .economics_engine import estimate_lp_economics

STABLES = {"USDC", "USDT", "USDG", "DAI", "USDS", "USDBC", "FRAX", "GHO", "LUSD"}
MAJORS = {"WETH", "ETH", "WBTC", "BTC"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def pair_class(pair: str) -> str:
    symbols = {x.strip().upper() for x in str(pair or "").replace("-", "/").split("/") if x.strip()}
    if len(symbols) >= 2 and symbols.issubset(STABLES):
        return "STABLE_STABLE"
    if symbols & MAJORS and symbols & STABLES:
        return "MAJOR_STABLE"
    if len(symbols & MAJORS) >= 2:
        return "MAJOR_MAJOR"
    if symbols & MAJORS:
        return "ALT_MAJOR"
    return "OTHER"


def _observed_fee_day(tracker: dict[str, Any]) -> tuple[float, str]:
    age = max(0.0, _f(tracker.get("age_days")))
    fee24 = max(0.0, _f(tracker.get("fees_24h_usd")))
    cumulative = max(0.0, _f(tracker.get("cumulative_earned_usd")))
    if age < 1.0:
        return 0.0, "INSUFFICIENT_UNDER_24H"
    if fee24 > 0:
        return fee24, "ROLLING_24H"
    if cumulative > 0:
        return cumulative / age, "SINCE_OPEN_DAILY_AVERAGE"
    return 0.0, "NO_FEE_EVIDENCE"


def calibration_samples(store) -> list[dict[str, Any]]:
    """Build model-vs-observed fee samples from positions LP Manager actually owns.

    Samples never alter booked P&L. They are only a forecasting calibration layer.
    A very young position is deliberately excluded because a few minutes of fees can
    annualise into nonsense.
    """
    out: list[dict[str, Any]] = []
    for position in store.list_positions():
        pid = str(position.get("id") or "")
        if not pid:
            continue
        tracker = store.get_setting(f"fees:tracker:{pid}", {}) or {}
        observed_day, observed_method = _observed_fee_day(tracker)
        if observed_day <= 0:
            continue
        snap = store.get_position_snapshot(pid) or {}
        pool = dict(snap.get("market") or {})
        if not pool:
            continue
        capital = max(_f(position.get("capital_value")), _f(position.get("current_value")))
        lower = _f(position.get("lower_price")); upper = _f(position.get("upper_price"))
        if capital <= 0 or lower <= 0 or upper <= lower:
            continue
        width_pct = (upper / lower - 1.0) * 100.0
        metrics = range_metrics(position)
        active_now = 100.0 if metrics.get("range_state") == "IN_RANGE" else 0.0
        model = estimate_lp_economics(
            pool,
            capital=capital,
            active_time_pct=active_now,
            width_pct=width_pct,
            regime={},
            lower_price=lower,
            upper_price=upper,
        )
        model_day = _f((model.get("estimated_fee_income") or {}).get("daily"))
        if model_day <= 0:
            continue
        ratio = max(0.20, min(5.0, observed_day / model_day))
        age = max(0.0, _f(tracker.get("age_days")))
        age_weight = min(1.0, age / 3.0)
        # A minimum weight preserves young-but-useful live evidence without letting it dominate.
        weight = max(0.15, age_weight)
        out.append({
            "position_id": pid,
            "pair": str(position.get("pair") or ""),
            "pair_class": pair_class(str(position.get("pair") or "")),
            "chain": str(position.get("chain") or "").upper(),
            "pool_address": str(position.get("pool_address") or snap.get("pool_address") or "").lower(),
            "sleeve": str(position.get("strategy_sleeve") or "").upper(),
            "fee_tier_bps": _f(model.get("fee_tier_bps")),
            "age_days": round(age, 4),
            "capital_usd": round(capital, 2),
            "observed_fee_day_usd": round(observed_day, 6),
            "observed_method": observed_method,
            "model_fee_day_usd": round(model_day, 6),
            "ratio": round(ratio, 4),
            "weight": round(weight, 4),
            "model_mode": model.get("mode"),
        })
    return out


def _weighted_geometric(rows: list[tuple[float, float]]) -> float:
    usable = [(max(0.05, r), max(0.0, w)) for r, w in rows if r > 0 and w > 0]
    if not usable:
        return 1.0
    total = sum(w for _, w in usable)
    if total <= 0:
        return 1.0
    return math.exp(sum(math.log(r) * w for r, w in usable) / total)


def fee_calibration_for_pool(store, pool: dict[str, Any], *, sleeve: str | None = None) -> dict[str, Any]:
    samples = calibration_samples(store)
    address = str(pool.get("pool_address") or "").lower()
    chain = str(pool.get("chain") or "").upper()
    pclass = pair_class(str(pool.get("pair") or ""))
    target_sleeve = str(sleeve or "").upper()
    fee_tier = _f(pool.get("fee_tier_bps") or pool.get("fee_bps"))

    weighted: list[tuple[float, float]] = []
    used: list[dict[str, Any]] = []
    for sample in samples:
        similarity = 0.0
        reasons: list[str] = []
        if address and sample["pool_address"] == address:
            similarity += 5.0; reasons.append("EXACT_POOL")
        if chain and sample["chain"] == chain:
            similarity += 1.25; reasons.append("SAME_CHAIN")
        if sample["pair_class"] == pclass:
            similarity += 1.50; reasons.append("SAME_PAIR_CLASS")
        if target_sleeve and sample["sleeve"] == target_sleeve:
            similarity += 0.75; reasons.append("SAME_SLEEVE")
        if fee_tier > 0 and abs(sample["fee_tier_bps"] - fee_tier) < 0.01:
            similarity += 1.0; reasons.append("SAME_FEE_TIER")
        if similarity <= 0:
            continue
        weight = sample["weight"] * similarity
        weighted.append((sample["ratio"], weight))
        used.append({**sample, "similarity_weight": round(weight, 4), "match": reasons})

    raw_factor = _weighted_geometric(weighted) if weighted else 1.0
    exact = [x for x in used if "EXACT_POOL" in x["match"]]
    effective_weight = sum(x["similarity_weight"] for x in used)
    # Shrink evidence toward 1x. A single young observation can nudge the model,
    # but cannot dominate it or create the V0.8.6 3x calibration failure.
    prior_strength = 8.0
    evidence_fraction = effective_weight / (effective_weight + prior_strength) if effective_weight > 0 else 0.0
    shrunk = math.exp(math.log(max(0.05, raw_factor)) * evidence_fraction) if weighted else 1.0
    exact_age = max([_f(x.get("age_days")) for x in exact] or [0.0])
    if exact_age < 3.0:
        lo, hi = 0.80, 1.25
    elif exact_age < 7.0:
        lo, hi = 0.65, 1.50
    else:
        lo, hi = 0.50, 2.00
    factor = max(lo, min(hi, shrunk))
    if exact and exact_age >= 7.0 and effective_weight >= 5.0:
        confidence = "HIGH"
    elif exact and exact_age >= 1.0:
        confidence = "MODERATE"
    elif used:
        confidence = "LOW"
    else:
        confidence = "UNAVAILABLE"
    return {
        "factor": round(factor, 4),
        "raw_factor": round(raw_factor, 4),
        "evidence_fraction": round(evidence_fraction, 4),
        "confidence": confidence,
        "sample_count": len(used),
        "exact_pool_samples": len(exact),
        "exact_pool_max_age_days": round(exact_age, 3),
        "pair_class": pclass,
        "samples": sorted(used, key=lambda x: x["similarity_weight"], reverse=True)[:8],
        "guardrails": {
            "minimum_sample_age_days": 1.0,
            "young_exact_pool_cap": [0.80, 1.25],
            "overall_cap": [0.50, 2.00],
            "method": "LOG_GEOMETRIC_SHRINKAGE_TO_1X",
        },
        "note": (
            "Live fee calibration is a conservative forecast correction. "
            "Samples under 24 hours are excluded and evidence is shrunk toward 1x."
        ),
    }
