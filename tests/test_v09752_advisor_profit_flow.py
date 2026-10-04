from pathlib import Path
import time

from lp_manager.candidate_universe_maintenance import CandidateUniverseMaintenanceService
from lp_manager.live_scout import preliminary_pool_evaluation
from lp_manager.portfolio_advisor import rank_opportunities


def test_unknown_pool_age_is_uncertainty_not_fake_zero_day_tactical_blocker():
    row = {
        "pair": "OP/WETH",
        "base_token": {"symbol": "OP"},
        "quote_token": {"symbol": "WETH"},
        "protocol": "UNISWAP_V3",
        "tvl_usd": 320_000,
        "volume_24h_usd": 135_000,
    }
    evaluation = preliminary_pool_evaluation(row)

    assert evaluation["quality"]["age_known"] is False
    assert evaluation["quality"]["age_days"] is None
    assert "POOL_AGE_UNKNOWN" in evaluation["risk_tactical"]["evidence"]
    assert "TACTICAL_HISTORY_TOO_SHORT" not in evaluation["risk_tactical"]["blockers"]
    assert evaluation["risk_tactical"]["eligible"] is True


def test_core_liquidity_stability_comes_from_pool_liquidity_not_hardcoded_sixty():
    row = {
        "pair": "WETH/USDC",
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDC"},
        "protocol": "UNISWAP_V3",
        "tvl_usd": 98_000_000,
        "volume_24h_usd": 17_000_000,
    }
    evaluation = preliminary_pool_evaluation(row)

    assert evaluation["quality"]["liquidity"] > 75
    assert "CORE_LIQUIDITY_QUALITY" not in evaluation["risk_core"]["blockers"]
    assert "CORE_HISTORY_TOO_SHORT" not in evaluation["risk_core"]["blockers"]
    assert evaluation["risk_core"]["eligible"] is True


def _advisor_row(i, score):
    return {
        "chain": "ETHEREUM",
        "pair": f"T{i}/USDC",
        "pool_address": "0x" + f"{i:040x}"[-40:],
        "sleeve": "TACTICAL_CAMPAIGN",
        "advisor_ranking_basis": "LEADERBOARD_OPPORTUNITY_SCORE",
        "leaderboard_rank": i,
        "opportunity_score": {"score": score},
        "evaluation": {
            "tactical_pre_score": 75,
            "core_pre_score": 60,
            "risk_tactical": {"eligible": True, "blockers": []},
            "risk_core": {"eligible": True, "blockers": []},
        },
        "economics": {
            "mode": "MODELLED",
            "capital_usd": 1000,
            "estimated_net_month_pct": 5,
            "estimated_operating_net_month_usd": 50,
            "volume_quality": {"factor": 1.0},
        },
        "regime": {"confidence": 50},
        "market_evidence_status": "LIVE_CURRENT",
        "profit_lab_readiness": {"ready": True, "status": "READY"},
        "deep_analysis": {
            "status": "DEEP_PROFITABLE",
            "fresh": True,
            "capital_usd": 1000,
            "horizon_days": 7,
            "expected_fees_usd": 40,
            "expected_net_usd": 40,
            "expected_intervention_cost_usd": 0,
        },
    }


def test_diversified_best_overall_can_use_full_deployable_capital_across_four_valid_pools():
    rows = [
        _advisor_row(1, 88),
        _advisor_row(2, 84),
        _advisor_row(3, 80),
        _advisor_row(4, 76),
    ]
    result = rank_opportunities(
        rows,
        available_capital=1000,
        reserve_pct=10,
        max_positions=4,
        sleeve_filter="ANY",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )

    assert len(result["allocations"]) == 4
    assert result["allocated"] >= 899.0
    assert result["unallocated"] <= 1.0
    assert result["candidate_gate_summary"]["eligible_after_gates"] == 4
    assert result["candidate_gate_summary"]["rejected"] == 0


class _Store:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


class _Universe:
    def __init__(self, now):
        self.now = now

    def cached(self, chain):
        return {
            "ok": True,
            "generated_at": self.now - 120,
            "shortlist": [],
            "summary": {},
        }

    def refresh(self, chain, **kwargs):
        raise AssertionError("nothing should be due yet")


def test_universe_status_explains_next_due_time_instead_of_looking_idle(monkeypatch):
    now = time.time()
    service = CandidateUniverseMaintenanceService(_Store(), _Universe(now), None)
    result = service.run_once()

    assert result["due_chains"] == 0
    assert result["next_due_seconds"] is not None
    assert 300 <= result["next_due_seconds"] <= 370
    assert CandidateUniverseMaintenanceService.TARGET_REFRESH_SECONDS == 8 * 60.0
    assert CandidateUniverseMaintenanceService.CYCLE_SECONDS == 45.0


def test_v09752_ui_explains_refresh_and_advisor_gate_outcomes():
    root = Path(__file__).resolve().parents[1]
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "all current · next refresh ~" in app
    assert "Why some capital stayed back." in app
    assert "Allocation gates:" in app
    assert "/static/app.js?v=0.9.7.5.2" in html
