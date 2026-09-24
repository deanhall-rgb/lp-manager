from __future__ import annotations

import math
import statistics
from typing import Any

from .asset_lens import pool_price_lens
from .economics_engine import estimate_lp_economics, infer_fee_tier_bps, volume_quality
from .market_regime import analyse_regime
from .pool_chain import read_v3_pool_metadata
from .profit_calibration import fee_calibration_for_pool
from .fee_metrics import forecast_fee_metrics, pool_fee_revenue_rate
from .price_units import assert_sane_display_lens
from .range_lab import analyse_range, candle_activity_fraction, generate_range_candidates, infer_candles_per_day
from .strategy_lab import _pool_from_onchain


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _percentile(values: list[float], q: float) -> float:
    rows = sorted(float(x) for x in values if math.isfinite(float(x)))
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    pos = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi:
        return rows[lo]
    frac = pos - lo
    return rows[lo] * (1.0 - frac) + rows[hi] * frac


def _horizon_persistence(month_factor: float, horizon_days: float) -> float:
    """Translate a monthly extrapolation haircut into a shorter-horizon haircut.

    A 7-day forecast should not suffer the same persistence discount as a 30-day
    extrapolation. The curve deliberately converges to the existing monthly factor.
    """
    p = max(0.15, min(1.0, _f(month_factor, 0.65)))
    h = max(0.25, min(30.4375, float(horizon_days)))
    return max(p, min(1.0, 1.0 - (1.0 - p) * (h / 30.4375) ** 0.65))


def _candidate_widths(sleeve: str, horizon_days: float) -> tuple[float, ...]:
    sleeve = str(sleeve or "CORE_INCOME").upper()
    h = max(1.0, float(horizon_days))
    if sleeve == "CORE_INCOME":
        if h <= 7:
            return (5, 6, 8, 10, 12.5, 15, 20, 25, 30)
        if h <= 14:
            return (6, 8, 10, 12.5, 15, 20, 25, 30, 35)
        return (8, 10, 12.5, 15, 20, 25, 30, 35, 40)
    if h <= 3:
        return (2.5, 3.5, 4.5, 6, 8, 10, 12.5, 15)
    if h <= 7:
        return (3, 4, 5, 6, 8, 10, 12.5, 15, 20)
    return (4, 5, 6, 8, 10, 12.5, 15, 20, 25)


def _candidate_skews(regime: dict[str, Any], sleeve: str) -> tuple[float, ...]:
    desired = _f(regime.get("range_skew_pct"))
    if str(sleeve).upper() == "CORE_INCOME":
        vals = {-12.5, -10, -5, 0, 5, 10, 12.5, round(desired / 2.5) * 2.5}
    else:
        vals = {-9, -6, -3, 0, 3, 6, 9, round(desired / 3.0) * 3.0}
    return tuple(sorted(vals))


def _walk_forward(
    candles: list[dict[str, Any]], *, half_width_pct: float, skew_pct: float,
    horizon_days: float, capital: float, pool: dict[str, Any], share: float,
    lifecycle_cost: float,
) -> dict[str, Any]:
    rows = [c for c in candles if _f(c.get("close")) > 0]
    if len(rows) < 12:
        return {"windows": 0}
    cpd = max(1e-9, infer_candles_per_day(rows))
    horizon_n = max(1, int(round(horizon_days * cpd)))
    if len(rows) <= horizon_n + 2:
        return {"windows": 0, "horizon_candles": horizon_n}
    step = max(1, int(round(cpd)))  # one historical entry per day
    fee_bps, _ = infer_fee_tier_bps(pool)
    fee_rate = fee_bps / 10_000.0
    results: list[dict[str, float]] = []
    for idx in range(0, len(rows) - horizon_n, step):
        entry = _f(rows[idx].get("close"))
        if entry <= 0:
            continue
        center = entry * (1.0 + skew_pct / 100.0)
        lower = center * (1.0 - half_width_pct / 100.0)
        upper = center * (1.0 + half_width_pct / 100.0)
        future = rows[idx + 1: idx + 1 + horizon_n]
        if not future:
            continue
        activities = [candle_activity_fraction(c, lower, upper) for c in future]
        active = sum(activities) / len(activities)
        captured_volume = sum(max(0.0, _f(c.get("volume"))) * a for c, a in zip(future, activities))
        gross_fees = captured_volume * fee_rate * max(0.0, min(1.0, share))
        states = []
        for c in future:
            close = _f(c.get("close"))
            states.append("BELOW" if close < lower else "ABOVE" if close > upper else "IN")
        excursions = 0; prev = "IN"
        for state in states:
            if prev == "IN" and state != "IN":
                excursions += 1
            prev = state
        net = gross_fees - excursions * max(0.0, lifecycle_cost)
        results.append({
            "net": net,
            "fees": gross_fees,
            "active_pct": active * 100.0,
            "excursions": float(excursions),
            "strict_survival": 1.0 if all(s == "IN" for s in states) else 0.0,
        })
    if not results:
        return {"windows": 0, "horizon_candles": horizon_n}
    fee_available = any(r["fees"] > 0 for r in results)
    split = max(1, int(len(results) * 0.70))
    train = results[:split]
    holdout = results[split:] or results[-max(1, len(results)//3):]

    def summary(group: list[dict[str, float]]) -> dict[str, Any]:
        net = [r["net"] for r in group]
        fees = [r["fees"] for r in group]
        return {
            "windows": len(group),
            "net_median_usd": round(statistics.median(net), 2),
            "net_mean_usd": round(statistics.mean(net), 2),
            "net_p25_usd": round(_percentile(net, 0.25), 2),
            "net_p75_usd": round(_percentile(net, 0.75), 2),
            "fees_median_usd": round(statistics.median(fees), 2),
            "active_time_median_pct": round(statistics.median(r["active_pct"] for r in group), 1),
            "strict_survival_pct": round(statistics.mean(r["strict_survival"] for r in group) * 100.0, 1),
            "excursions_median": round(statistics.median(r["excursions"] for r in group), 1),
        }
    return {
        "windows": len(results),
        "horizon_candles": horizon_n,
        "train": summary(train),
        "holdout": summary(holdout),
        "fee_economics_available": fee_available,
        "method": "DAILY_WALK_FORWARD_FIXED_GEOMETRY_CURRENT_LIQUIDITY_SHARE_PROXY",
    }


def _normalise(values: list[float], value: float) -> float:
    if not values:
        return 50.0
    lo = min(values); hi = max(values)
    if hi - lo < 1e-9:
        return 50.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def _load_pool_and_history(
    market, chain: str, address: str, history_days: int,
    pool_fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str, str | None]:
    """Load one V3 pool with an execution-price history in the same units as ticks.

    GeckoTerminal OHLC is token-USD history. That is only a valid proxy for a pool
    execution ratio when the human quote asset is a USD stablecoin. V0.8.6 used the
    provider's arbitrary base token, which made USDG/WETH look like ~1 USDG/WETH.
    """
    chain = str(chain or "").upper()
    onchain = read_v3_pool_metadata(chain, address)
    pool = dict(pool_fallback or {}) if pool_fallback else None
    pool_error = None

    if pool is None:
        try:
            if hasattr(market, "pool"):
                pool = market.pool(chain, address)
            elif hasattr(market, "resolve_pool"):
                resolved, pool = market.resolve_pool(chain, address)
                chain = str(resolved or chain).upper()
                onchain = read_v3_pool_metadata(chain, address)
        except Exception as exc:
            pool_error = str(exc)

    if pool is None and onchain.get("ok"):
        pool = _pool_from_onchain(chain, address, onchain, pool_fallback)
    if pool is None:
        raise RuntimeError(pool_error or str(onchain.get("error") or "Pool context unavailable"))

    provider_base_before = str((pool.get("base_token") or {}).get("symbol") or "").upper()
    provider_quote_before = str((pool.get("quote_token") or {}).get("symbol") or "").upper()

    if onchain.get("ok"):
        # Rebuild the provider row through the on-chain lens so pair orientation,
        # token roles and fee tier are authoritative.
        pool = _pool_from_onchain(chain, address, onchain, pool)
        pool["onchain"] = onchain
        lens = dict(onchain.get("price_lens") or {})
        assert_sane_display_lens(lens)

    timeframe = "hour" if history_days <= 45 else "day"
    minimum = 48 if timeframe == "hour" else 20
    provider = "GECKOTERMINAL_POOL_OHLC"
    warning = None
    candles: list[dict[str, Any]] = []

    # Identify the human execution base/quote (e.g. WETH quoted in USDC/USDG).
    unit_label = str(((onchain.get("price_lens") or {}).get("unit_label") or pool.get("price_unit_label") or ""))
    quote_symbol = base_symbol = ""
    if " per " in unit_label:
        quote_symbol, base_symbol = [x.strip().upper() for x in unit_label.split(" per ", 1)]
    stable_symbols = {"USDC","USDT","USDG","DAI","USDS","USDBC","FRAX","GHO","LUSD"}

    # For stable-quoted pools, fetch the human base token's USD OHLC. WETH/USDG
    # then uses WETH USD history, not USDG's ~$1 history.
    gecko_token = "base"
    provider_base = provider_base_before or str((pool.get("base_token") or {}).get("symbol") or "").upper()
    provider_quote = provider_quote_before or str((pool.get("quote_token") or {}).get("symbol") or "").upper()
    if base_symbol and provider_quote == base_symbol and provider_base != base_symbol:
        gecko_token = "quote"
    pool["_history_token"] = gecko_token

    # For non-stable quoted pairs, token USD is not the execution ratio. Prefer the
    # pair-ratio history reconstructed from token histories when available.
    if quote_symbol and quote_symbol not in stable_symbols and hasattr(market, "alchemy_pool_history") and onchain.get("ok"):
        try:
            ratio_history = market.alchemy_pool_history(chain, onchain, history_days, timeframe=timeframe)
            if len(ratio_history) >= minimum:
                candles = ratio_history
                provider = "ALCHEMY_PAIR_RATIO_HISTORY"
        except Exception as exc:
            warning = str(exc)

    if not candles:
        try:
            try:
                candles = market.ohlcv_days(chain, address, history_days, timeframe=timeframe, token=gecko_token)
            except TypeError:
                # Test/simple adapters from older releases do not expose token=.
                candles = market.ohlcv_days(chain, address, history_days, timeframe=timeframe)
        except Exception as exc:
            candles = []
            warning = str(exc)

    if len(candles) < minimum and hasattr(market, "alchemy_pool_history") and onchain.get("ok"):
        try:
            fallback = market.alchemy_pool_history(chain, onchain, history_days, timeframe=timeframe)
        except Exception:
            fallback = []
        if len(fallback) >= minimum:
            candles = fallback
            provider = "ALCHEMY_PAIR_RATIO_HISTORY"

    if len(candles) < minimum:
        raise ValueError(
            f"Only {len(candles)} {timeframe} historical samples available"
            + (f"; {warning}" if warning else "")
        )
    return pool, onchain, candles, provider, warning

def recommend_profit_range(
    market, store, chain: str, address: str, *, horizon_days: float = 7.0,
    capital: float = 1000.0, sleeve: str = "AUTO", monthly_target_pct: float = 10.0,
    history_days: int | None = None, pool_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Money-first LP range recommendation with explicit evidence and validation.

    The engine never promotes a target to a forecast. It searches range geometries
    for expected net fee cashflow over the requested holding period, validates the
    geometry on historical walk-forward windows, then overlays current regime and
    live fee calibration from positions actually owned by the operator.
    """
    horizon = max(1.0 / 24.0, min(90.0, float(horizon_days)))
    capital = max(1.0, float(capital))
    hist_days = int(history_days or max(30, min(180, round(horizon * 6))))
    pool, onchain, candles, provider, warning = _load_pool_and_history(market, chain, address, hist_days, pool_fallback)
    # Current execution price is authoritative on-chain. Historical token-USD
    # candles are only a path proxy and must never overwrite the live pool ratio.
    spot = _f((onchain.get("price_lens") or {}).get("current")) or _f(candles[-1].get("close")) or _f(pool.get("base_token_price_usd"))
    if spot <= 0:
        raise ValueError("No usable current price")
    cpd = 24 if hist_days <= 45 else 1
    regime_rows = candles
    if cpd == 1:
        try:
            try:
                recent = market.ohlcv_days(
                    str(chain).upper(), address, min(30, hist_days),
                    timeframe="hour", token=str(pool.get("_history_token") or "base"),
                )
            except TypeError:
                recent = market.ohlcv_days(str(chain).upper(), address, min(30, hist_days), timeframe="hour")
            if len(recent) >= 48:
                regime_rows = recent; cpd = 24
        except Exception:
            pass
    regime = analyse_regime(regime_rows, candles_per_day=cpd)

    sleeve_u = str(sleeve or "AUTO").upper()
    if sleeve_u in {"AUTO", "ANY", ""}:
        symbols = {str((pool.get("base_token") or {}).get("symbol") or "").upper(), str((pool.get("quote_token") or {}).get("symbol") or "").upper()}
        stables = {"USDC","USDT","USDG","DAI","USDS","USDBC","FRAX","GHO"}; majors = {"WETH","ETH","WBTC","BTC"}
        sleeve_u = "CORE_INCOME" if symbols & stables and symbols & majors else "TACTICAL_CAMPAIGN"

    calibration = fee_calibration_for_pool(store, {**pool, "chain": str(chain).upper()}, sleeve=sleeve_u)
    widths = _candidate_widths(sleeve_u, horizon)
    skews = _candidate_skews(regime, sleeve_u)
    candidates = [c for c in generate_range_candidates(spot, half_widths_pct=widths, skews_pct=skews) if c.lower <= spot <= c.upper]
    rows: list[dict[str, Any]] = []
    target_horizon = capital * max(0.0, float(monthly_target_pct)) / 100.0 * horizon / 30.4375

    for candidate in candidates:
        analysis = analyse_range(candles, candidate.lower, candidate.upper, horizon_days=horizon)
        interventions_month = _f(analysis.get("excursions")) * (30.4375 / max(1.0, hist_days))
        econ = estimate_lp_economics(
            pool, capital=capital, active_time_pct=_f(analysis.get("average_horizon_activity_pct")),
            width_pct=candidate.width_pct, regime=regime,
            expected_interventions_per_month=interventions_month, lifecycle_cost_per_intervention=1.5,
            lower_price=candidate.lower, upper_price=candidate.upper,
        )
        if econ.get("mode") == "INSUFFICIENT_DATA":
            continue
        share = _f(econ.get("liquidity_share_baseline_pct")) / 100.0
        wf = _walk_forward(
            candles, half_width_pct=(candidate.upper / candidate.center - 1.0) * 100.0,
            skew_pct=candidate.skew_pct, horizon_days=horizon, capital=capital, pool=pool,
            share=share, lifecycle_cost=1.5,
        )
        current_day = _f((econ.get("estimated_fee_income") or {}).get("daily"))
        p_month = _f(econ.get("persistence_haircut_factor"), 0.65)
        p_h = _horizon_persistence(p_month, horizon)
        calibrated_day = current_day * _f(calibration.get("factor"), 1.0)
        forecast_fees = calibrated_day * horizon * p_h
        intervention_cost = max(0.0, interventions_month) * 1.5 * horizon / 30.4375
        current_net = forecast_fees - intervention_cost
        train = wf.get("train") or {}
        holdout = wf.get("holdout") or {}
        historical_fee_ok = bool(wf.get("fee_economics_available"))
        train_median = _f(train.get("net_median_usd"), current_net) if historical_fee_ok else current_net
        train_p25 = _f(train.get("net_p25_usd"), current_net * 0.55) if historical_fee_ok else current_net * 0.55
        train_p75 = _f(train.get("net_p75_usd"), current_net * 1.35) if historical_fee_ok else current_net * 1.35
        # Current conditions matter most. Older training windows regularise the current
        # forecast; the recent holdout remains untouched as validation evidence.
        expected_net = current_net * 0.68 + train_median * 0.32 if historical_fee_ok else current_net
        low = min(current_net * 0.55, train_p25)
        high = max(current_net * 1.35, train_p75)
        current_alignment = max(0.0, 100.0 - min(100.0, abs(candidate.skew_pct - _f(regime.get("range_skew_pct"))) * 4.0))
        rows.append({
            "lower": candidate.lower, "upper": candidate.upper, "center": candidate.center,
            "half_width_pct": round((candidate.upper / candidate.center - 1.0) * 100.0, 3),
            "width_pct": round(candidate.width_pct, 3), "skew_pct": round(candidate.skew_pct, 3),
            "analysis": analysis, "economics": econ, "walk_forward": wf,
            "forecast": {
                "horizon_days": round(horizon, 3),
                "fee_day_current_calibrated_usd": round(calibrated_day, 4),
                "horizon_persistence_factor": round(p_h, 4),
                "expected_fees_usd": round(forecast_fees, 2),
                "expected_intervention_cost_usd": round(intervention_cost, 2),
                "expected_net_usd": round(expected_net, 2),
                "expected_net_pct": round(expected_net / capital * 100.0, 3),
                "low_net_usd": round(low, 2), "high_net_usd": round(high, 2),
                "target_horizon_usd": round(target_horizon, 2),
                "target_attainment_pct": round(expected_net / target_horizon * 100.0, 1) if target_horizon > 0 else 0.0,
            },
            "regime_alignment_score": round(current_alignment, 1),
        })

    if not rows:
        raise ValueError("No candidate range had sufficient economics data")
    profit_values = [_f(r["forecast"].get("expected_net_pct")) for r in rows]
    train_values = [_f((r.get("walk_forward",{}).get("train") or {}).get("net_median_usd")) / capital * 100.0 for r in rows]
    for row in rows:
        profit_norm = _normalise(profit_values, _f(row["forecast"].get("expected_net_pct")))
        train_pct = _f((row.get("walk_forward",{}).get("train") or {}).get("net_median_usd")) / capital * 100.0
        train_norm = _normalise(train_values, train_pct) if row.get("walk_forward",{}).get("fee_economics_available") else profit_norm
        active = _f(row["analysis"].get("average_horizon_activity_pct"))
        strict = _f(row["analysis"].get("strict_horizon_survival_pct"))
        alignment = _f(row.get("regime_alignment_score"))
        interventions = _f(row["analysis"].get("excursions"))
        intervention_penalty = min(18.0, interventions * (3.0 if sleeve_u == "CORE_INCOME" else 1.5))
        score = 0.48 * profit_norm + 0.24 * train_norm + 0.12 * active + 0.06 * strict + 0.10 * alignment - intervention_penalty
        row["profit_score"] = round(max(0.0, min(100.0, score)), 1)
        row["price_lens"] = pool_price_lens(pool, lower=row["lower"], upper=row["upper"], current=spot)
    rows.sort(key=lambda r: (r["profit_score"], _f(r["forecast"].get("expected_net_usd"))), reverse=True)
    best = rows[0]

    # Directional alternatives are plans, not automatic recommendations. They make
    # the single-sided option explicit when the current regime is directional.
    directional: list[dict[str, Any]] = []
    breakout = str(regime.get("breakout_state") or "")
    if "UPSIDE" in breakout or _f(regime.get("range_skew_pct")) >= 5:
        directional.append({"type":"UPPER_SINGLE_SIDED_ENTRY","trigger":"Price enters the range above spot","purpose":"Sell/convert inventory progressively into an upside continuation rather than deploying balanced liquidity immediately."})
    if "DOWNSIDE" in breakout or _f(regime.get("range_skew_pct")) <= -5:
        directional.append({"type":"LOWER_SINGLE_SIDED_ENTRY","trigger":"Price enters the range below spot","purpose":"Accumulate the base asset on a pullback rather than forcing balanced liquidity at current price."})

    evidence = {
        "onchain_pool_metadata": "AVAILABLE" if onchain.get("ok") else "DEGRADED",
        "history_provider": provider,
        "history_samples": len(candles),
        "history_days": hist_days,
        "historical_volume": "AVAILABLE" if any(_f(c.get("volume")) > 0 for c in candles) else "UNAVAILABLE",
        "current_pool_tvl_volume": "AVAILABLE" if _f(pool.get("tvl_usd")) > 0 and _f(pool.get("volume_24h_usd")) > 0 else "DEGRADED",
        "live_fee_calibration": calibration.get("confidence"),
        "market_regime": regime.get("label") or regime.get("breakout_state"),
        "provider_warning": warning,
    }
    confidence_points = 30
    confidence_points += 20 if onchain.get("ok") else 0
    confidence_points += 20 if provider == "GECKOTERMINAL_POOL_OHLC" else 8
    confidence_points += 15 if evidence["historical_volume"] == "AVAILABLE" else 0
    confidence_points += 15 if calibration.get("confidence") in {"HIGH","MODERATE"} else 5 if calibration.get("confidence") == "LOW" else 0
    confidence = "HIGH" if confidence_points >= 80 else "MODERATE" if confidence_points >= 60 else "LOW"

    return {
        "objective": "MAXIMISE_EXPECTED_NET_LP_FEE_PROFIT_FOR_HOLDING_PERIOD",
        "chain": str(chain).upper(), "pool_address": address, "pair": pool.get("pair"),
        "sleeve": sleeve_u, "capital_usd": round(capital, 2), "horizon_days": round(horizon, 3),
        "monthly_target_pct": round(float(monthly_target_pct), 3), "spot": spot,
        "price_lens": pool_price_lens(pool, current=spot), "pool": pool,
        "regime": regime, "fee_calibration": calibration, "evidence": evidence,
        "confidence": confidence, "confidence_score": min(100, confidence_points),
        "recommended_range": {**best, "rank": 1},
        "alternatives": [{**r, "rank": i + 1} for i, r in enumerate(rows[:8])],
        "directional_alternatives": directional,
        "target": {
            "horizon_target_usd": round(target_horizon, 2),
            "recommended_expected_net_usd": best["forecast"]["expected_net_usd"],
            "attainment_pct": best["forecast"]["target_attainment_pct"],
            "clears_target": best["forecast"]["expected_net_usd"] >= target_horizon if target_horizon > 0 else None,
        },
        "why": [
            "Expected net fee cashflow over the requested holding period is the primary ranking input.",
            "Older history contributes to range selection; recent holdout windows are kept separate as validation evidence rather than used to choose the winner.",
            "Current market regime changes range skew/alignment rather than being treated as a binary enter/do-not-enter switch.",
            "Observed fees from owned LPs calibrate the model when sufficiently mature live evidence exists.",
        ],
        "limitations": [
            "Historical active tick liquidity is not fully reconstructed; current active-liquidity share is used as a proxy in walk-forward fee tests.",
            "Forecasts are estimates, not guaranteed returns; volume, competition and price path can change after entry.",
            "Single-sided directional ranges are presented as optional triggered plans and are not assumed to earn fees before price enters them.",
        ],
    }
