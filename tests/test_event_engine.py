from lp_manager.event_engine import detect_material_event, review_due


def test_core_ai_is_quieter_than_strategy_review():
    now=100000
    row=review_due(strategy_sleeve="CORE_INCOME", last_strategy_review_at=now-5*3600, last_ai_review_at=now-5*3600, now=now)
    assert row["strategy_due"] is True
    assert row["ai_due"] is False


def test_range_break_wakes_ai():
    row=detect_material_event(
        {"current_price":100,"range_state":"IN_RANGE","apr_current":30},
        {"current_price":106,"range_state":"OUT_ABOVE","nearest_edge_pct":0,"apr_current":30},
        strategy_sleeve="CORE_INCOME",
    )
    assert row["wake_strategy"] is True
    assert row["wake_ai"] is True


def test_edge_watch_fires_on_crossing_not_every_snapshot():
    first = detect_material_event({"nearest_edge_pct": 8.0}, {"nearest_edge_pct": 4.5}, strategy_sleeve="CORE_INCOME")
    repeated = detect_material_event({"nearest_edge_pct": 4.5}, {"nearest_edge_pct": 4.2}, strategy_sleeve="CORE_INCOME")
    assert first["wake_strategy"]
    assert not repeated["wake_strategy"]

