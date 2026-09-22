from lp_manager.range_lab import analyse_range, rank_range_candidates


def candles():
    rows=[]
    base=100.0
    for i in range(240):
        price=base + (i % 24 - 12) * 0.25
        rows.append({"timestamp": i*3600, "open":price, "high":price+1, "low":price-1, "close":price, "volume":1000})
    return rows


def test_wide_range_more_durable_than_tight_range():
    rows=candles()
    tight=analyse_range(rows, 99.5, 100.5, horizon_days=3, candles_per_day=24)
    wide=analyse_range(rows, 95, 105, horizon_days=3, candles_per_day=24)
    assert wide["active_time_pct"] > tight["active_time_pct"]
    assert wide["durability_score"] > tight["durability_score"]


def test_core_range_ranker_returns_candidates():
    ranked=rank_range_candidates(candles(), 100, sleeve="CORE_INCOME", half_widths_pct=(5,10,20), skews_pct=(0,), horizon_days=3)
    assert len(ranked)==3
    assert ranked[0]["range_score"] >= ranked[-1]["range_score"]
