from contextlib import contextmanager
from pathlib import Path
import time

import pytest

from lp_manager.deep_analysis_rotation import DeepAnalysisRotationService
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard
from lp_manager.product import ECONOMICS_MODEL_VERSION


class _Store:
    def __init__(self):
        self.settings = {}
        self.snapshots = []
        self.recorded = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def record_forecast_snapshot(self, result, model_version=""):
        self.recorded.append((result, model_version))
        return {"id": f"snap-{len(self.recorded)}"}

    def list_forecast_snapshots(self, limit=500):
        return list(self.snapshots)[:limit]

    def list_opportunities(self, limit=1000):
        return []


class _Leaderboard:
    def __init__(self, rows):
        self.rows = rows

    def rebuild(self, limit=25):
        rows = [dict(x) for x in self.rows[:limit]]
        return {
            "ok": True,
            "rows": rows,
            "funnel": {
                "deep_analysed": sum(
                    1 for x in rows
                    if (x.get("deep_analysis") or {}).get("status") != "NOT_ANALYSED"
                ),
                "allocation_confirmed": sum(1 for x in rows if x.get("allocation_confirmed")),
            },
        }


class _Coordinator:
    def __init__(self):
        self.background_entries = 0

    @contextmanager
    def background_work(self):
        self.background_entries += 1
        yield


def _row(rank, status="NOT_ANALYSED", ready=True, score=80):
    return {
        "rank": rank,
        "chain": "ETHEREUM",
        "pool_address": "0x" + f"{rank:040x}"[-40:],
        "pair": f"T{rank}/USDC",
        "protocol_version": "V3",
        "leaderboard_eligible": True,
        "profit_lab_readiness": {"ready": ready, "status": "READY"},
        "opportunity_score": {"score": score},
        "deep_analysis": {"status": status},
    }


def test_rotation_targets_top_25_plus_challengers_and_prioritises_recheck_then_unanalysed_then_stale(monkeypatch):
    rows = [
        _row(1, "NOT_ANALYSED", score=95),
        _row(2, "DEEP_STALE", score=94),
        _row(3, "DEEP_RECHECK_REQUIRED", score=93),
        _row(4, "DEEP_PROFITABLE", score=92),
        _row(5, "NOT_ANALYSED", ready=False, score=91),
    ] + [_row(i, "NOT_ANALYSED", score=90-i) for i in range(6, 31)]

    service = DeepAnalysisRotationService(object(), _Store(), object(), _Leaderboard(rows))
    targets = service.targets()

    assert all(int(x["rank"]) <= 40 for x in targets)
    assert targets[0]["rank"] == 3
    assert targets[0]["rotation_zone"] == "TOP_25"
    assert any(x["rotation_zone"] == "CHALLENGER_26_40" for x in targets)
    assert all(x["rank"] != 4 for x in targets)
    assert all(x["rank"] != 5 for x in targets)


def test_rotation_skips_retrying_top_rows_and_immediately_backfills_from_rank_26_plus():
    rows = (
        [_row(i, "NOT_ANALYSED", score=100-i) for i in range(1, 5)]
        + [_row(i, "DEEP_PROFITABLE", score=100-i) for i in range(5, 26)]
        + [_row(i, "NOT_ANALYSED", score=100-i) for i in range(26, 31)]
    )
    service = DeepAnalysisRotationService(object(), _Store(), object(), _Leaderboard(rows))
    now = time.time()
    for rank in range(1, 5):
        row = rows[rank - 1]
        service._retry_after[service._key(row)] = now + 600

    targets = service.targets()

    assert [x["rank"] for x in targets[:5]] == [26, 27, 28, 29, 30]
    assert all(x["rotation_zone"] == "CHALLENGER_26_40" for x in targets[:5])
    assert service.TARGET_LIMIT == 40
    assert service.BOARD_LIMIT == 25


def test_rotation_runs_one_standard_background_profit_plan(monkeypatch):
    store = _Store()
    row = _row(1, "NOT_ANALYSED")
    board = _Leaderboard([row])
    coordinator = _Coordinator()
    captured = {}

    monkeypatch.setattr(
        "lp_manager.deep_analysis_rotation.money_context",
        lambda settings, store: {"display_currency": "GBP"},
    )
    monkeypatch.setattr(
        "lp_manager.deep_analysis_rotation.display_amount_to_usd",
        lambda value, settings, store: 1333.33,
    )

    def fake_recommend(market, store_arg, chain, address, **kwargs):
        captured.update({"chain": chain, "address": address, **kwargs})
        return {
            "economics_model_version": ECONOMICS_MODEL_VERSION,
            "chain": chain,
            "pool_address": address,
            "pair": "HLX/USDC",
            "sleeve": "TACTICAL_CAMPAIGN",
            "capital_usd": kwargs["capital"],
            "horizon_days": kwargs["horizon_days"],
            "recommended_range": {
                "lower": 0.04,
                "upper": 0.05,
                "range_quality_score": 80,
                "forecast": {
                    "expected_net_usd": 40,
                    "modelled_position_apr_pct": 210,
                },
            },
        }

    monkeypatch.setattr(
        "lp_manager.deep_analysis_rotation.recommend_profit_range",
        fake_recommend,
    )

    service = DeepAnalysisRotationService(object(), store, object(), board, coordinator)
    result = service.run_once(max_items=1)

    assert result["processed"] == 1
    assert result["session_completed"] == 1
    assert coordinator.background_entries == 1
    assert captured["horizon_days"] == 7.0
    assert captured["capital"] == pytest.approx(1333.33)
    assert captured["sleeve"] == "AUTO"
    assert captured["compare_fee_tiers"] is False

    saved, model_version = store.recorded[0]
    assert model_version == "v0.9.7.5-background"
    assert saved["analysis_source"] == "BACKGROUND_LEADERBOARD_V0975"
    assert saved["leaderboard_deep_basis"]["capital_display"] == 1000.0
    assert saved["leaderboard_deep_basis"]["display_currency"] == "GBP"
    assert saved["leaderboard_deep_basis"]["horizon_days"] == 7.0


def test_standard_background_snapshot_remains_leaderboard_basis_after_manual_short_hold():
    now = time.time()
    store = _Store()
    address = "0x" + "9" * 40
    standard_payload = {
        "analysis_source": "BACKGROUND_LEADERBOARD_V0975",
        "leaderboard_deep_basis": {
            "capital_display": 1000,
            "display_currency": "GBP",
            "horizon_days": 7,
        },
        "economics_model_version": ECONOMICS_MODEL_VERSION,
        "recommended_range": {
            "range_quality_score": 80,
            "forecast": {
                "expected_net_usd": 40,
                "modelled_position_apr_pct": 210,
                "position_economics_evidence_class": "MODELLED_CONCENTRATED_POSITION",
                "position_economics_confidence": "MODERATE",
            },
        },
    }
    manual_payload = {
        "economics_model_version": ECONOMICS_MODEL_VERSION,
        "recommended_range": {
            "range_quality_score": 88,
            "forecast": {
                "expected_net_usd": 12,
                "modelled_position_apr_pct": 400,
                "position_economics_evidence_class": "MODELLED_CONCENTRATED_POSITION",
                "position_economics_confidence": "MODERATE",
            },
        },
    }
    store.snapshots = [
        {
            "id": "manual-newer",
            "created_at": now,
            "chain": "ETHEREUM",
            "pool_address": address,
            "horizon_days": 1,
            "capital_usd": 1333,
            "expected_net_usd": 12,
            "payload": manual_payload,
        },
        {
            "id": "auto-standard",
            "created_at": now - 30,
            "chain": "ETHEREUM",
            "pool_address": address,
            "horizon_days": 7,
            "capital_usd": 1333,
            "expected_net_usd": 40,
            "payload": standard_payload,
        },
    ]
    board = OpportunityLeaderboard(store, object())
    deep = board._deep_index()[("ETHEREUM", address.lower())]

    assert deep["forecast_id"] == "auto-standard"
    assert deep["standard_basis"] is True
    assert deep["horizon_days"] == 7
    assert deep["expected_net_usd"] == 40


def test_v0975_ui_and_lifecycle_expose_background_rotation():
    root = Path(__file__).resolve().parents[1]
    app = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    html = (root / "lp_manager" / "static" / "index.html").read_text(encoding="utf-8")

    assert "Auto deep rotation" in app
    assert "Queued for automatic Profit Lab analysis" in app
    assert "deep_analysis_rotation.start_background()" in api
    assert "deep_analysis_rotation.stop_background()" in api
    assert '"background_deep_analysis"' in api
    assert "/static/app.js?v=0.9.7.5.4" in html
