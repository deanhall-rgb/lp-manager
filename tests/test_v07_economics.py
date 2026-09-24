from lp_manager.economics_engine import estimate_lp_economics, estimate_replay_economics


def pool(volume):
    return {"pair":"WETH/USDC","tvl_usd":10_000_000,"volume_24h_usd":volume,"fee_tier_bps":5}


def test_more_volume_produces_more_estimated_fees():
    low=estimate_lp_economics(pool(2_000_000),capital=1000,active_time_pct=80,width_pct=40)
    high=estimate_lp_economics(pool(8_000_000),capital=1000,active_time_pct=80,width_pct=40)
    assert high["estimated_fee_income"]["daily"] > low["estimated_fee_income"]["daily"]
    assert high["estimated_net_month_usd"] > low["estimated_net_month_usd"]
    assert high["assumptions"]


def test_replay_economics_uses_observed_volume_but_labels_estimate():
    candles=[{"timestamp":i*3600,"close":100+i*.1,"volume":100_000} for i in range(72)]
    r=estimate_replay_economics(candles,[1.0]*72,pool(2_000_000),capital=1000,width_pct=30)
    assert r["mode"] == "ESTIMATED_HISTORICAL_VOLUME_CURRENT_TVL_PROXY"
    assert r["estimated_fees_total_usd"] > 0
    assert r["estimated"] is True
