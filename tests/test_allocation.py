from lp_manager.allocation import AllocationLimits, suggest_allocation


def test_tactical_allocation_respects_total_book_cap():
    positions=[{"status":"OPEN","current_value":1900,"strategy_sleeve":"TACTICAL_CAMPAIGN","chain":"BASE","pair":"X/Y"}]
    row=suggest_allocation(
        total_portfolio_value=10000, available_cash=3000, positions=positions,
        opportunity={"preferred_sleeve":"TACTICAL_CAMPAIGN","preferred_score":90,"chain":"ARBITRUM"},
        limits=AllocationLimits(max_tactical_total_pct=20),
    )
    assert row["suggested_capital"] <= 100


def test_weak_candidate_gets_zero_even_with_cash():
    row=suggest_allocation(
        total_portfolio_value=10000, available_cash=5000, positions=[],
        opportunity={"preferred_sleeve":"CORE_INCOME","preferred_score":50,"chain":"BASE"},
    )
    assert row["suggested_capital"] == 0
    assert "CANDIDATE_SCORE_BELOW_ALLOCATION_THRESHOLD" in row["blockers"]


def test_core_high_score_can_receive_bounded_capital():
    row=suggest_allocation(
        total_portfolio_value=10000, available_cash=5000, positions=[],
        opportunity={"preferred_sleeve":"CORE_INCOME","preferred_score":95,"chain":"BASE"},
    )
    assert row["eligible"] is True
    assert 0 < row["suggested_capital"] <= 4000
