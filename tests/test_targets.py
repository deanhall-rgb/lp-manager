from lp_manager.targets import performance_targets


def test_targets_translate_monthly_rate_to_periods():
    row=performance_targets(capital=10000,target_monthly_pct=10,actual_today=40,actual_7d=250,actual_30d=900)
    assert round(row["month"]["target"]) == 1000
    assert row["guardrail"].startswith("REPORTING_ONLY")
    assert row["today"]["target"] > 0
