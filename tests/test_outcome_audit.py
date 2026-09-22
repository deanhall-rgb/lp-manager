from lp_manager.outcome_audit import audit_range_outcome


def test_detects_exit_and_reentry():
    rows = [{"close": 105}, {"close": 121}, {"close": 110}]
    r = audit_range_outcome(lower=90, upper=120, entry_price=100, future_rows=rows)
    assert r["classification"] == "EXITED_AND_REENTERED"
