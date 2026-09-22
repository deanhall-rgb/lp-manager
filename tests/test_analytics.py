from lp_manager.analytics import range_metrics, position_economics


def test_range_metrics_in_range():
    r = range_metrics({"lower_price": 14.75, "upper_price": 21.5, "current_price": 15.8})
    assert r["range_state"] == "IN_RANGE"
    assert round(r["distance_lower_pct"], 2) == 7.12
    assert round(r["distance_upper_pct"], 2) == 36.08
    assert 0 < r["progress_pct"] < 100


def test_range_metrics_out_above():
    r = range_metrics({"lower_price": 10, "upper_price": 20, "current_price": 21})
    assert r["range_state"] == "OUT_ABOVE"


def test_position_economics():
    e = position_economics({
        "capital_value": 1000,
        "current_value": 1020,
        "unclaimed_fees": 30,
        "realised_fees": 10,
        "estimated_il": -5,
        "gas_costs": 3,
    })
    assert e["net_profit"] == 52
    assert e["net_return_pct"] == 5.2
