from lp_manager.replay import demo_replay_candles, run_replay


def test_core_replay_has_no_lookahead_and_is_quieter():
    rows = demo_replay_candles("CORE_TREND", points=240)
    r = run_replay(rows, sleeve="CORE_INCOME", warmup_candles=72)
    assert r["no_lookahead"] is True
    assert 0 < r["summary"]["strategy_reviews"] < r["summary"]["replay_candles"] / 2
    assert r["economics"]["mode"] == "UNAVAILABLE_WITH_OHLC_ONLY"
    assert r["range_selection"]["chosen"]["candidate"]["lower"] < r["range_selection"]["chosen"]["candidate"]["upper"]


def test_tactical_replay_generates_actionable_decisions():
    rows = demo_replay_candles("TACTICAL_BREAKOUT", points=180)
    r = run_replay(rows, sleeve="TACTICAL_CAMPAIGN", pair="DELTA/WETH", warmup_candles=48)
    action = r["summary"]["severity_counts"].get("ACTION", 0)
    assert action > 0
    assert r["summary"]["material_events"] > 0


def test_observed_fee_series_is_summed_without_fabrication():
    rows = demo_replay_candles("CORE_TREND", points=100)
    for row in rows:
        row["position_fee_value"] = 0.25
    r = run_replay(rows, sleeve="CORE_INCOME", warmup_candles=30)
    assert r["economics"]["mode"] == "OBSERVED_POSITION_FEE_SERIES"
    assert r["economics"]["observed_fees"] == 17.5
