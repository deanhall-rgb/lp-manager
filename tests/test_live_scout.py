from lp_manager.live_scout import preliminary_pool_evaluation


def test_major_stable_pool_scores_as_core_candidate():
    pool={"protocol":"UNISWAP_V3","base_token":{"symbol":"WETH"},"quote_token":{"symbol":"USDC"},"tvl_usd":50_000_000,"volume_24h_usd":20_000_000,"pool_created_at":"2024-01-01T00:00:00Z"}
    result=preliminary_pool_evaluation(pool)
    assert result["preliminary"] is True
    assert result["core_pre_score"] > 70
    assert result["preferred_sleeve"] == "CORE_INCOME"


def test_unknown_low_liquidity_pool_not_forced_into_sleeve():
    pool={"protocol":"UNISWAP_V3","base_token":{"symbol":"AAA"},"quote_token":{"symbol":"BBB"},"tvl_usd":5_000,"volume_24h_usd":1_000}
    result=preliminary_pool_evaluation(pool)
    assert result["core_pre_score"] < 72
