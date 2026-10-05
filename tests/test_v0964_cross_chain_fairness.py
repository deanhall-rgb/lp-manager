from pathlib import Path
from types import SimpleNamespace
import time

from lp_manager.candidate_universe import CandidateUniverse


class FakeStore:
    def __init__(self):
        self.settings = {}

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def list_positions(self, status=None):
        return []


class FakeDiscovery:
    pass


def test_candidate_universe_fresh_cache_reuses_recent_snapshot():
    store = FakeStore()
    store.settings["candidate_universe:ETHEREUM"] = {
        "ok": True,
        "chain": "ETHEREUM",
        "generated_at": time.time() - 30,
        "summary": {"shortlisted": 12},
        "providers": [],
        "shortlist": [],
    }
    universe = CandidateUniverse(SimpleNamespace(), store, FakeDiscovery())

    result = universe.fresh_cached("ETHEREUM", max_age_seconds=180)

    assert result is not None
    assert result["fresh_cache"] is True
    assert result["cached"] is True
    assert 0 <= result["cache_age_seconds"] <= 180


def test_candidate_universe_rejects_old_snapshot_as_fresh_cache():
    store = FakeStore()
    store.settings["candidate_universe:ETHEREUM"] = {
        "ok": True,
        "chain": "ETHEREUM",
        "generated_at": time.time() - 400,
        "summary": {"shortlisted": 12},
        "providers": [],
        "shortlist": [],
    }
    universe = CandidateUniverse(SimpleNamespace(), store, FakeDiscovery())

    assert universe.fresh_cached("ETHEREUM", max_age_seconds=180) is None


def test_v0964_advisor_defaults_include_all_six_supported_chains_and_shared_universe():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")

    assert 'default_chains=["ETHEREUM","BASE","ARBITRUM","OPTIMISM","POLYGON","ROBINHOOD_CHAIN"]' in api
    assert 'result["data_source"]="LEADERBOARD_ALIGNED_SHARED_UNIVERSE"' in api
    assert "advisor_board_limit=40" in api
    assert "_fair_chain_candidates(enriched,8)" not in api
    assert "_shared_universe_snapshot" in api
    assert "_fair_chain_candidates" in api
    assert 'live.market.network_pools(chain,page=1)' not in api


def test_v0964_fairness_shortlists_by_multiple_independent_dimensions():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")

    assert 'float(r.get("tvl_usd") or 0)' in api
    assert 'float(r.get("volume_24h_usd") or 0)' in api
    assert 'core_pre_score' in api
    assert 'tactical_pre_score' in api
    assert 'estimated_operating_net_month_usd' in api


def test_v0964_soak_diagnostics_are_exposed_to_ui():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="app-version-label"' in html
    assert "SHARED DISCOVERY" in html
    assert 'result["universe_diagnostics"]' in api
    assert '"fresh_cache_hits"' in api
    assert '"rate_limit_events"' in api
    assert '"ranking_chains"' in api
    assert "Shared Candidate Universe:" in js
    assert "fresh-cache reuse" in js
    assert "rate-limit events" in js


def test_v0964_profit_lab_and_execution_boundaries_remain_explicit():
    root = Path(__file__).parents[1]
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "Profit Lab remains separate" in html
    assert "browser wallet" in html.lower()
