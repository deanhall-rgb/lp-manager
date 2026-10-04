from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from lp_manager.evidence_warmer import EvidenceWarmService
from lp_manager.market_data import GeckoTerminalClient
from lp_manager.profit_engine import warm_profit_history
from lp_manager.provider_coordinator import (
    ProviderCoordinator,
    ProviderDeferred,
    ProviderPolicy,
)


class FakeStore:
    def __init__(self):
        self.settings = {}
        self.positions = []
        self.opportunities = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def list_positions(self, status=None):
        if status is None:
            return list(self.positions)
        return [x for x in self.positions if str(x.get("status") or "").upper() == str(status).upper()]

    def list_opportunities(self, limit=100):
        return list(self.opportunities)[:limit]


class FakeUniverse:
    def __init__(self, rows=None):
        self.rows = rows or []

    def cached(self, chain):
        return {"ok": bool(self.rows), "chain": chain, "shortlist": list(self.rows)}


def _fresh_candles(count=72, close=10.0):
    now = int(time.time())
    start = now - (count - 1) * 3600
    return [
        {
            "timestamp": start + i * 3600,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 0.0,
        }
        for i in range(count)
    ]


def test_provider_coordinator_reuses_fresh_cache():
    coordinator = ProviderCoordinator()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"value": 42}

    first = coordinator.request("ALCHEMY", "same", loader, ttl_seconds=60)
    second = coordinator.request("ALCHEMY", "same", loader, ttl_seconds=60)

    assert first == second == {"value": 42}
    assert calls["n"] == 1
    status = coordinator.status()
    assert status["providers"]["ALCHEMY"]["requests"] == 1
    assert status["providers"]["ALCHEMY"]["cache_hits"] >= 1


def test_provider_coordinator_coalesces_identical_inflight_request():
    coordinator = ProviderCoordinator(
        policies={"ALCHEMY": ProviderPolicy(min_gap_seconds=0, max_concurrent=2)}
    )
    barrier = threading.Barrier(3)
    calls = {"n": 0}
    results = []
    errors = []

    def loader():
        calls["n"] += 1
        time.sleep(0.15)
        return {"ok": True, "loaded": calls["n"]}

    def worker():
        try:
            barrier.wait(timeout=2)
            results.append(
                coordinator.request(
                    "ALCHEMY", "history:QNT", loader,
                    ttl_seconds=60, wait_timeout_seconds=3,
                )
            )
        except Exception as exc:  # pragma: no cover - assertion reports detail.
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=4)

    assert not errors
    assert len(results) == 2
    assert results[0] == results[1]
    assert calls["n"] == 1
    assert coordinator.status()["providers"]["ALCHEMY"]["coalesced_waits"] >= 1


def test_background_provider_work_yields_to_recent_interactive_request():
    coordinator = ProviderCoordinator(
        policies={
            "GECKOTERMINAL": ProviderPolicy(
                min_gap_seconds=0,
                max_concurrent=1,
                interactive_quiet_seconds=30,
            )
        }
    )

    assert coordinator.request(
        "GECKOTERMINAL", "interactive", lambda: {"ok": True}, ttl_seconds=1
    ) == {"ok": True}

    with coordinator.background_work():
        with pytest.raises(ProviderDeferred):
            coordinator.request(
                "GECKOTERMINAL", "background-new-key",
                lambda: {"should": "not run"},
                ttl_seconds=1,
            )

    status = coordinator.status()
    assert status["providers"]["GECKOTERMINAL"]["background_deferred"] >= 1


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.get_calls = 0

    def get(self, *args, **kwargs):
        self.get_calls += 1
        return FakeResponse({"data": []})


def test_gecko_client_uses_shared_coordinator_cache():
    session = FakeSession()
    coordinator = ProviderCoordinator(
        policies={"GECKOTERMINAL": ProviderPolicy(min_gap_seconds=0, max_concurrent=1)}
    )
    market = GeckoTerminalClient(session=session, coordinator=coordinator)

    market._get("/networks/eth/pools", {"page": 1})
    market._get("/networks/eth/pools", {"page": 1})

    assert session.get_calls == 1
    assert coordinator.status()["providers"]["GECKOTERMINAL"]["cache_hits"] >= 1


def test_profit_history_warmer_uses_durable_cache_without_provider_lookup():
    store = FakeStore()
    address = "0x" + "a" * 40
    store.settings[f"profit:history:v09642:ETHEREUM:{address}:hour"] = {
        "saved_at": time.time(),
        "provider": "TEST_CACHE",
        "candles": _fresh_candles(),
    }

    class Market:
        def pool(self, *args, **kwargs):
            raise AssertionError("fresh durable history should avoid provider lookup")

    result = warm_profit_history(
        Market(), store, "ETHEREUM", address, history_days=30,
        pool_fallback={"chain": "ETHEREUM", "pool_address": address, "pair": "QNT/WETH"},
    )

    assert result["ok"] is True
    assert result["status"] == "CACHE_READY"
    assert result["warmed"] is False
    assert result["samples"] == 72


def test_evidence_warmer_recognises_ready_cache_and_records_status():
    store = FakeStore()
    address = "0x" + "b" * 40
    store.settings[f"profit:history:v09642:ETHEREUM:{address}:hour"] = {
        "saved_at": time.time(),
        "provider": "TEST_CACHE",
        "candles": _fresh_candles(),
    }
    row = {
        "chain": "ETHEREUM",
        "pool_address": address,
        "pair": "QNT/WETH",
        "protocol": "UNISWAP_V3",
        "research_ready": True,
        "discovery_score": 90,
    }
    universe = FakeUniverse([row])

    class Market:
        pass

    warmer = EvidenceWarmService(store, Market(), universe, ProviderCoordinator())
    warmer.enqueue(row)
    result = warmer.run_once(max_items=1)

    assert result["processed"] == 1
    assert result["ready"] == 1
    assert result["failed"] == 0
    assert result["results"][0]["status"] == "CACHE_READY"
    assert store.settings["evidence_warmer:status"]["ready"] == 1


def test_v0972_api_wires_one_shared_coordinator_and_background_evidence_warmer():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    market = (root / "lp_manager" / "market_data.py").read_text(encoding="utf-8")
    pool_chain = (root / "lp_manager" / "pool_chain.py").read_text(encoding="utf-8")

    assert "provider_coordinator = ProviderCoordinator(store)" in api
    assert "LiveDataService(settings, store, provider_coordinator=provider_coordinator)" in api
    assert "coordinator=provider_coordinator" in api
    assert "EvidenceWarmService(" in api
    assert "evidence_warmer.start_background()" in api
    assert "evidence_warmer.enqueue" in api
    assert '@app.get("/api/system/providers")' in api
    assert 'self.coordinator.request(' in market
    assert 'configure_provider_coordinator(provider_coordinator)' in api
    assert '"RPC",key' in pool_chain


def test_v0972_ui_exposes_coordination_without_changing_ranking_surface():
    root = Path(__file__).parents[1]
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert "/static/styles.css?v=0.9.7.5.1" in html
    assert "/static/app.js?v=0.9.7.5.1" in html
    assert "Provider coordinator" in js
    assert "coalesced" in js
    assert "Profit evidence queued" in js
    assert "Provider coordination:" in js
    # v0.9.7.2 is plumbing only: Advisor still uses the existing ranking endpoint.
    assert "runPortfolioAdvisor()" in js
    assert "/api/portfolio-advisor" in js
