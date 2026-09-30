from pathlib import Path

from lp_manager.portfolio_advisor import bounded_advisor_calibration, rank_opportunities


def _row(
    pair="CASHCAT/WETH",
    *,
    sleeve="TACTICAL_CAMPAIGN",
    score=90,
    net_pct=12,
    chain="ROBINHOOD_CHAIN",
    address="0x1111111111111111111111111111111111111111",
    evidence="LIVE_CURRENT",
):
    return {
        "pair": pair,
        "chain": chain,
        "pool_address": address,
        "sleeve": sleeve,
        "market_evidence_status": evidence,
        "evaluation": {
            "core_pre_score": score,
            "tactical_pre_score": score,
            "risk_core": {"eligible": True, "blockers": []},
            "risk_tactical": {"eligible": True, "blockers": []},
        },
        "economics": {
            "capital_usd": 1000,
            "estimated_net_month_pct": net_pct,
            "estimated_net_month_usd": net_pct * 10,
            "estimated_operating_net_month_usd": net_pct * 10,
            "mode": "ESTIMATED_FROM_VOLUME_TVL_RANGE",
            "volume_quality": {"factor": 1.0},
            "confidence": "MODERATE",
        },
        "regime": {"confidence": 70},
    }


def _position(value=250, *, address="0x1111111111111111111111111111111111111111"):
    return {
        "id": "P7",
        "status": "OPEN",
        "monitoring_class": "ACTIVE",
        "chain": "ROBINHOOD_CHAIN",
        "pair": "CASHCAT/WETH",
        "pool_address": address,
        "strategy_sleeve": "TACTICAL_CAMPAIGN",
        "current_value": value,
    }


def test_existing_pool_exposure_turns_advisor_amount_into_incremental_addition():
    result = rank_opportunities(
        [_row()],
        available_capital=1000,
        reserve_pct=10,
        open_positions=[_position(250)],
    )

    assert result["allocations"]
    allocation = result["allocations"][0]
    # Existing $250 plus a new $125 reaches the same 30% tactical single-pool
    # concentration ceiling already used by the Advisor rather than pretending
    # the existing LP does not exist.
    assert allocation["incremental_amount"] == 125.0
    assert allocation["existing_exposure"]["pool_value"] == 250.0
    assert allocation["post_allocation"]["pool_value"] == 375.0
    assert allocation["post_allocation"]["pool_pct"] == 30.0
    assert allocation["concentration_penalty"] > 0
    assert result["portfolio_context"]["existing_open_value"] == 250.0


def test_existing_exposure_can_block_only_the_increment_without_rejecting_pool_quality():
    result = rank_opportunities(
        [_row()],
        available_capital=400,
        reserve_pct=10,
        open_positions=[_position(600)],
    )

    assert result["allocations"] == []
    assert result["near_misses"]
    reasons = " ".join(result["near_misses"][0]["reject_reasons"])
    assert "existing pool concentration" in reasons
    assert "risk gate" not in reasons


def test_persisted_cache_cannot_beat_fresh_live_market_evidence():
    stale = _row(
        pair="STALE/WETH",
        score=99,
        net_pct=40,
        address="0x2222222222222222222222222222222222222222",
        evidence="PERSISTED_CACHE",
    )
    live = _row(
        pair="LIVE/WETH",
        score=82,
        net_pct=7,
        address="0x3333333333333333333333333333333333333333",
        evidence="LIVE_CURRENT",
    )

    result = rank_opportunities([stale, live], available_capital=1000, reserve_pct=10)

    assert result["allocations"]
    assert result["allocations"][0]["pair"] == "LIVE/WETH"
    stale_ranked = next(x for x in result["ranked"] if x["pair"] == "STALE/WETH")
    assert any("not refreshed" in x for x in stale_ranked["reject_reasons"])


def test_owned_history_is_exact_pool_only_and_bounded_to_ten_percent():
    econ = {
        "capital_usd": 1000,
        "estimated_operating_net_month_usd": 100,
        "estimated_net_month_usd": 100,
        "estimated_net_month_pct": 10,
    }
    high = bounded_advisor_calibration(
        econ,
        {"factor": 2.0, "confidence": "HIGH", "exact_pool_samples": 2, "sample_count": 3},
    )
    low = bounded_advisor_calibration(
        econ,
        {"factor": 0.1, "confidence": "MODERATE", "exact_pool_samples": 1, "sample_count": 1},
    )
    unrelated = bounded_advisor_calibration(
        econ,
        {"factor": 2.0, "confidence": "LOW", "exact_pool_samples": 0, "sample_count": 9},
    )

    assert high["estimated_operating_net_month_usd"] == 110.0
    assert high["advisor_calibration"]["factor"] == 1.1
    assert low["estimated_operating_net_month_usd"] == 90.0
    assert low["advisor_calibration"]["factor"] == 0.9
    assert unrelated["estimated_operating_net_month_usd"] == 100.0
    assert unrelated["advisor_calibration"]["applied"] is False


def test_v0963_ui_explains_incremental_portfolio_aware_allocation():
    root = Path(__file__).parents[1]
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert "v0.9.6.3" in html
    assert "Portfolio-aware" in html
    assert "Existing pool" in js
    assert "New capital plan" in js
    assert "live model; no ranking boost from owned history" in js


def test_diversified_tactical_candidates_can_use_full_new_money_budget_across_multiple_pools():
    rows = [
        _row(pair=f"TACT{i}/WETH", score=90 - i, net_pct=10 - i * 0.5, address=f"0x{i+10:040x}")
        for i in range(4)
    ]
    result = rank_opportunities(rows, available_capital=1000, reserve_pct=10)

    # The capital field means NEW money. With a 10% reserve, all £900 is
    # available to allocate if enough fresh/risk-eligible candidates exist.
    # Diversified mode still limits each Tactical pool to 30% of deployable
    # new capital, so no single Tactical candidate can take more than £270.
    assert result["deployable"] == 900.0
    assert result["allocated"] >= 899.99
    assert result["unallocated"] <= 0.01
    assert all(a["incremental_amount"] <= 270.01 for a in result["allocations"])


def test_existing_tactical_exposure_does_not_shrink_the_new_money_budget():
    rows = [
        _row(pair=f"TACT{i}/WETH", score=90 - i, net_pct=10 - i * 0.5, address=f"0x{i+20:040x}")
        for i in range(4)
    ]
    existing = _position(200, address="0x9999999999999999999999999999999999999999")
    result = rank_opportunities(rows, available_capital=1000, reserve_pct=10, open_positions=[existing])

    assert result["portfolio_context"]["existing_open_value"] == 200.0
    assert result["deployable"] == 900.0
    assert result["allocated"] >= 899.99
    assert result["unallocated"] <= 0.01


def test_ui_explains_existing_book_is_context_not_a_deduction_from_new_capital():
    root = Path(__file__).parents[1]
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert "New capital plan" in js
    assert "does not reduce your new-capital budget" in js
    assert "${money(deployable)} available to deploy" in js

