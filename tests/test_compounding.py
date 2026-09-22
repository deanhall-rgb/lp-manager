from lp_manager.compounding import fee_action


def test_tactical_banks_material_fees():
    row=fee_action(sleeve="TACTICAL_CAMPAIGN", unclaimed_fees_value=50, collect_cost=1)
    assert row["action"] == "COLLECT_BANK"


def test_small_fees_are_left_alone():
    row=fee_action(sleeve="CORE_INCOME", unclaimed_fees_value=2, collect_cost=1)
    assert row["action"] == "LEAVE"


def test_core_compounds_when_incremental_return_covers_cost():
    row=fee_action(sleeve="CORE_INCOME", unclaimed_fees_value=500, collect_cost=0.2, reinvest_cost=0.2, expected_apr_pct=40, horizon_days=30)
    assert row["action"] == "COLLECT_COMPOUND"
