import time
from contextlib import contextmanager
from pathlib import Path

from lp_manager.candidate_universe_maintenance import CandidateUniverseMaintenanceService
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard
from lp_manager.portfolio_advisor import rank_opportunities


class _Store:
    def __init__(self):
        self.settings = {}
        self.forecasts = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def list_forecast_snapshots(self, limit=500):
        return list(self.forecasts)[:limit]

    def list_opportunities(self, limit=1000):
        return []


class _Universe:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.refreshed = []

    def cached(self, chain):
        return self.snapshots.get(chain, {"ok": False, "summary": {}, "shortlist": []})

    def refresh(self, chain, **kwargs):
        self.refreshed.append((chain, kwargs))
        snap = dict(self.snapshots.get(chain) or {})
        snap["ok"] = True
        snap["generated_at"] = time.time()
        snap.setdefault("shortlist", [])
        snap.setdefault("summary", {})
        self.snapshots[chain] = snap
        return snap


class _Coordinator:
    @contextmanager
    def background_work(self):
        yield


def _candidate(pair="AAA/USDC", address=None):
    return {
        "pair": pair,
        "pool_address": address or "0x" + "1" * 40,
        "protocol": "UNISWAP_V3",
        "version": "V3",
        "research_ready": True,
        "economic_validation": "CROSS_VALIDATED",
        "profit_lab_readiness": {"ready": True, "status": "READY", "label": "PROFIT READY"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 2_000_000,
        "gross_fee_apr_proxy": 50,
        "turnover_24h": 2,
        "discovery_score": 75,
    }


def test_stale_universe_remains_visible_on_persistent_leaderboard():
    store = _Store()
    universe = _Universe({
        "ETHEREUM": {
            "ok": True,
            "generated_at": time.time() - 3 * 3600,
            "summary": {
                "v3_discovered": 1,
                "shortlisted": 1,
                "live_validated": 1,
                "research_ready": 1,
                "profit_lab_ready": 1,
            },
            "shortlist": [_candidate()],
        }
    })
    board = OpportunityLeaderboard(store, universe).rebuild(chains=["ETHEREUM"], limit=25)

    assert board["funnel"]["leaderboard_eligible"] == 1
    assert board["funnel"]["shown"] == 1
    assert len(board["rows"]) == 1
    assert board["rows"][0]["freshness"]["state"] == "STALE"
    assert board["rows"][0]["market_fresh_for_allocation"] is False
    assert board["rows"][0]["allocation_confirmed"] is False


def test_universe_maintenance_refreshes_oldest_due_chain_at_background_priority():
    now = time.time()
    snapshots = {
        "ETHEREUM": {"ok": True, "generated_at": now - 2000, "shortlist": [_candidate()]},
        "BASE": {"ok": True, "generated_at": now - 1000, "shortlist": [_candidate("BBB/USDC", "0x" + "2" * 40)]},
        "ARBITRUM": {"ok": True, "generated_at": now, "shortlist": []},
        "OPTIMISM": {"ok": True, "generated_at": now, "shortlist": []},
        "POLYGON": {"ok": True, "generated_at": now, "shortlist": []},
        "ROBINHOOD_CHAIN": {"ok": True, "generated_at": now, "shortlist": []},
    }
    store = _Store()
    universe = _Universe(snapshots)
    service = CandidateUniverseMaintenanceService(store, universe, _Coordinator())

    result = service.run_once()

    assert universe.refreshed
    assert universe.refreshed[0][0] == "ETHEREUM"
    kwargs = universe.refreshed[0][1]
    assert kwargs["preserve_existing_on_failure"] is True
    assert kwargs["graph_limit"] == 500
    assert kwargs["shortlist_limit"] == 40
    assert kwargs["validate_limit"] == 20
    assert result["session_refreshed"] == 1


def _advisor_row(pair, address, opportunity_score, pre_score):
    return {
        "chain": "ETHEREUM",
        "pair": pair,
        "pool_address": address,
        "sleeve": "TACTICAL_CAMPAIGN",
        "advisor_ranking_basis": "LEADERBOARD_OPPORTUNITY_SCORE",
        "leaderboard_rank": 1 if opportunity_score > 80 else 2,
        "opportunity_score": {"score": opportunity_score},
        "evaluation": {
            "tactical_pre_score": pre_score,
            "core_pre_score": pre_score,
            "risk_tactical": {"eligible": True, "blockers": []},
            "risk_core": {"eligible": True, "blockers": []},
        },
        "economics": {
            "mode": "MODELLED",
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
            "expected_net_usd": 38,
            "expected_intervention_cost_usd": 2,
        },
    }


def test_best_overall_advisor_uses_leaderboard_opportunity_score_as_cross_pool_basis():
    rows = [
        _advisor_row("LEADER/USDC", "0x" + "3" * 40, 88, 20),
        _advisor_row("OLD-SCREEN/USDC", "0x" + "4" * 40, 62, 98),
    ]
    result = rank_opportunities(
        rows,
        available_capital=1000,
        reserve_pct=10,
        max_positions=2,
        sleeve_filter="ANY",
        allocation_mode="DIVERSIFIED",
        open_positions=[],
    )

    assert result["ranked"][0]["pair"] == "LEADER/USDC"
    assert result["ranked"][0]["ranking_basis"] == "LEADERBOARD_OPPORTUNITY_SCORE"
    assert result["ranked"][0]["base_portfolio_score"] == 88


def test_v09751_ui_and_api_expose_universe_upkeep_and_leaderboard_aligned_advisor():
    root = Path(__file__).resolve().parents[1]
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "Universe upkeep" in app
    assert "Leaderboard-aligned Advisor" in app
    assert "candidate_universe_maintenance.start_background()" in api
    assert "candidate_universe_maintenance.stop_background()" in api
    assert "LEADERBOARD_ALIGNED_SHARED_UNIVERSE" in api
    assert "/static/app.js?v=0.9.7.5.4" in html
