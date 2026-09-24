from lp_manager.market_regime import analyse_regime


def test_bullish_regime_skews_range_upward():
    rows=[]
    price=100.0
    for i in range(24*35):
        price *= 1.00035
        if i > 24*30:
            price *= 1.0009
        rows.append({"timestamp":i*3600,"close":price,"high":price*1.002,"low":price*.998,"volume":1000+i*2})
    r=analyse_regime(rows)
    assert r["direction"] == "BULLISH"
    assert r["range_skew_pct"] > 0
    assert r["returns"]["7d_pct"] > 0
