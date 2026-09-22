from lp_manager.scout_policy import ScoutCandidate, evaluate_candidate


def test_blue_chip_weth_usdc_prefers_core():
    c = ScoutCandidate(
        chain="BASE", protocol="UNISWAP_V3", pair="WETH/USDC",
        tvl_usd=25_000_000, volume_24h_usd=12_000_000, pool_age_days=700,
        chain_quality=95, asset_conviction=96, token_quality=98,
        liquidity_stability=92, fee_consistency=85, in_range_probability_30d=79,
        expected_monthly_net_pct=11, expected_daily_net_pct=0.18,
        gas_drag_pct=0.08, sentiment_score=0.35, momentum_alignment=70,
        downside_inventory_desirable=True,
    )
    result = evaluate_candidate(c)
    assert result["core"]["eligible"] is True
    assert result["preferred_sleeve"] == "CORE_INCOME"


def test_small_bullish_high_fee_pool_can_be_tactical_only():
    c = ScoutCandidate(
        chain="ROBINHOOD_CHAIN", protocol="UNISWAP_V3", pair="DELTA/WETH",
        tvl_usd=450_000, volume_24h_usd=900_000, pool_age_days=12,
        chain_quality=75, asset_conviction=70, token_quality=68,
        liquidity_stability=62, fee_consistency=66, in_range_probability_30d=35,
        expected_monthly_net_pct=35, expected_daily_net_pct=1.2,
        gas_drag_pct=0.2, sentiment_score=0.55, momentum_alignment=82,
        downside_inventory_desirable=True,
    )
    result = evaluate_candidate(c)
    assert result["core"]["eligible"] is False
    assert result["tactical"]["eligible"] is True
    assert result["preferred_sleeve"] == "TACTICAL_CAMPAIGN"


def test_tactical_requires_bullish_evidence_if_downside_inventory_is_risky_asset():
    c = ScoutCandidate(
        chain="BASE", protocol="UNISWAP_V3", pair="ALT/WETH",
        tvl_usd=500_000, pool_age_days=8, chain_quality=90, asset_conviction=65,
        token_quality=65, liquidity_stability=60, fee_consistency=60,
        expected_daily_net_pct=1.0, sentiment_score=-0.2, momentum_alignment=40,
        downside_inventory_desirable=True,
    )
    result = evaluate_candidate(c)
    assert "BULLISH_THESIS_NOT_CONFIRMED" in result["tactical"]["blockers"]
