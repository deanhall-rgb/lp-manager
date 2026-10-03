from lp_manager.opportunity_score import SCORE_VERSION, score_opportunity


def base_row(**overrides):
    row = {
        "pair": "WETH/USDC",
        "chain": "ETHEREUM",
        "pool_address": "0xabc",
        "tvl_usd": 5_000_000,
        "volume_24h_usd": 8_000_000,
        "turnover_24h": 1.6,
        "fee_apr_proxy": 35.0,
        "economic_validation": "CROSS_VALIDATED",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "freshness": {"state": "FRESH"},
        "portfolio_overlap": False,
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 1000.0,
            "horizon_days": 3.0,
            "expected_fees_usd": 12.0,
            "expected_intervention_cost_usd": 2.0,
            "expected_net_usd": 10.0,
            "range_quality_score": 82.0,
        },
    }
    row.update(overrides)
    return row


def test_opportunity_score_is_transparent_and_bounded():
    result = score_opportunity(base_row())
    assert result["version"] == SCORE_VERSION
    assert 0 <= result["score"] <= 100
    assert set(result["components"]) == {
        "net_economics",
        "range_durability",
        "liquidity_quality",
        "activity_quality",
        "risk_quality",
        "evidence_quality",
    }
    assert sum(result["weights"].values()) == 100
    assert result["confidence"] == "HIGH"


def test_fresh_deep_positive_beats_same_pool_without_deep_analysis():
    strong = score_opportunity(base_row())
    screen_only = score_opportunity(base_row(
        deep_analysis={"status": "NOT_ANALYSED", "fresh": False},
    ))
    assert strong["score"] > screen_only["score"]
    assert screen_only["score_cap"] == 79.0
    assert "deep Profit Lab" in screen_only["cap_reason"]


def test_extreme_turnover_is_penalised_even_with_huge_volume():
    normal = score_opportunity(base_row())
    extreme = score_opportunity(base_row(
        tvl_usd=1_000_000,
        volume_24h_usd=150_000_000,
        turnover_24h=150.0,
    ))
    assert extreme["components"]["activity_quality"] < normal["components"]["activity_quality"]
    assert extreme["components"]["risk_quality"] < normal["components"]["risk_quality"]


def test_raw_apr_cannot_buy_top_rank_without_quality_or_evidence():
    durable = score_opportunity(base_row(
        fee_apr_proxy=25.0,
        deep_analysis={
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 1000.0,
            "horizon_days": 30.0,
            "expected_fees_usd": 45.0,
            "expected_intervention_cost_usd": 5.0,
            "expected_net_usd": 40.0,
            "range_quality_score": 88.0,
        },
    ))
    hot_junk = score_opportunity(base_row(
        tvl_usd=30_000,
        volume_24h_usd=6_000_000,
        turnover_24h=200.0,
        fee_apr_proxy=900.0,
        economic_validation="UNVERIFIED",
        profit_lab_readiness={"ready": False, "status": "MARKET_VALIDATION_REQUIRED"},
        freshness={"state": "AGING"},
        deep_analysis={"status": "NOT_ANALYSED", "fresh": False},
    ))
    assert hot_junk["score"] <= 50.0
    assert durable["score"] > hot_junk["score"]


def test_negative_deep_economics_caps_score():
    result = score_opportunity(base_row(
        deep_analysis={
            "status": "DEEP_NON_POSITIVE",
            "fresh": True,
            "capital_usd": 1000.0,
            "horizon_days": 3.0,
            "expected_fees_usd": 4.0,
            "expected_intervention_cost_usd": 9.0,
            "expected_net_usd": -5.0,
            "range_quality_score": 90.0,
        }
    ))
    assert result["components"]["net_economics"] == 0.0
    assert result["score"] <= 55.0
    assert result["score_cap"] == 55.0
