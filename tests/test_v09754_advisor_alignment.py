from pathlib import Path

from lp_manager.portfolio_advisor import rank_opportunities


def _row(pair, sleeve, monthly_pct, score=80):
    net_7d = 1000.0 * monthly_pct / 100.0 * 7.0 / 30.0
    return {
        "chain": "ETHEREUM",
        "pair": pair,
        "pool_address": "0x" + pair.encode().hex()[:40].ljust(40, "0"),
        "sleeve": sleeve,
        "advisor_ranking_basis": "LEADERBOARD_OPPORTUNITY_SCORE",
        "opportunity_score": {"score": score},
        "evaluation": {
            "core_pre_score": 85,
            "tactical_pre_score": 85,
            "risk_core": {"eligible": True, "blockers": []},
            "risk_tactical": {"eligible": True, "blockers": []},
        },
        "economics": {
            "mode": "MODELLED",
            "capital_usd": 1000,
            "estimated_net_month_pct": monthly_pct,
            "estimated_operating_net_month_usd": monthly_pct * 10,
            "volume_quality": {"factor": 1.0},
        },
        "regime": {"confidence": 50},
        "market_evidence_status": "LIVE_CURRENT",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "label": "DEEP +VE",
            "fresh": True,
            "capital_usd": 1000,
            "horizon_days": 7,
            "expected_fees_usd": net_7d,
            "expected_net_usd": net_7d,
            "expected_intervention_cost_usd": 0,
            "standard_basis": True,
        },
    }


def test_tactical_filter_can_allocate_same_standard_deep_evidence_as_best_overall():
    rows = [
        _row("NVDAON/USDC", "TACTICAL_CAMPAIGN", 40, 82),
        _row("SPCXON/USDC", "TACTICAL_CAMPAIGN", 55, 80),
        _row("HLX/USDC", "TACTICAL_CAMPAIGN", 22, 79),
    ]
    best = rank_opportunities(
        rows,
        available_capital=1000,
        reserve_pct=10,
        monthly_target_pct=10,
        max_positions=4,
        sleeve_filter="ANY",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )
    tactical = rank_opportunities(
        rows,
        available_capital=1000,
        reserve_pct=10,
        monthly_target_pct=10,
        max_positions=4,
        sleeve_filter="TACTICAL_CAMPAIGN",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )

    assert [x["pair"] for x in tactical["allocations"]] == [x["pair"] for x in best["allocations"]]
    assert tactical["allocated"] == best["allocated"]
    assert tactical["allocated"] > 0


def test_core_filter_reports_target_failure_instead_of_missing_deep_evidence():
    result = rank_opportunities(
        [_row("WBTC/WETH", "CORE_INCOME", 2.7, 70)],
        available_capital=1000,
        reserve_pct=10,
        monthly_target_pct=10,
        max_positions=4,
        sleeve_filter="CORE_INCOME",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )

    assert result["allocations"] == []
    assert "monthly profit target" in result["reason"]
    miss = result["near_misses"][0]
    assert miss["deep_analysis"]["status"] == "DEEP_PROFITABLE"
    assert any("below 10.0% target" in x for x in miss["reject_reasons"])


def test_v09754_api_and_ui_keep_all_advisor_modes_on_one_leaderboard_path():
    root = Path(__file__).resolve().parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "advisor_board_limit=40" in api
    assert 'result["data_source"]="LEADERBOARD_ALIGNED_SHARED_UNIVERSE"' in api
    assert "candidates=opportunity_leaderboard.attach_analysis_state(candidates)" not in api
    assert "monthly_target_pct:targetPct" in app
    assert "Leaderboard-aligned Advisor:" in app
    assert "top-25 pending" in app
    assert "challengers queued" in app
    assert "+${num(x.equivalent_alternatives_hidden||0,0)} eqv hidden" in app
    assert "/static/app.js?v=0.9.7.6" in html
