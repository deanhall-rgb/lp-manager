from __future__ import annotations

from pathlib import Path

import pytest

from lp_manager.db import Store
from lp_manager import live_positions as live_positions_module
from lp_manager.live_positions import _live_v3_fees_from_growth, _position_lifecycle_events, _closed_position_final, finalise_closed_positions_from_store
from lp_manager.models import Position
from lp_manager.range_lab import analyse_range
from lp_manager.position_identity import authoritative_opening_tx
from lp_manager.profit_engine import _decision_horizon_evidence, _history_matches_spot, _history_cache_is_fresh, _select_regime_aware_best


class _Call:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class _PoolFunctions:
    def feeGrowthGlobal0X128(self):
        return _Call(1000 << 128)

    def feeGrowthGlobal1X128(self):
        return _Call(2000 << 128)

    def ticks(self, tick):
        if int(tick) < 0:
            return _Call((0, 0, 100 << 128, 200 << 128, 0, 0, 0, True))
        return _Call((0, 0, 200 << 128, 300 << 128, 0, 0, 0, True))


class _Pool:
    functions = _PoolFunctions()


def test_wallet_buttons_and_execution_buttons_use_multi_selector():
    js = (Path(__file__).parents[1] / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert "$$('.wallet-provider-choice').forEach" in js
    assert "$$('.exec-send').forEach" in js
    assert "\n    $('.wallet-provider-choice').forEach" not in js
    assert ";$('.exec-send').forEach" not in js


def test_live_fee_growth_math_includes_unrealised_accrual_not_only_stored_owed():
    pos = [0, None, None, None, 0, -100, 100, 10, 600 << 128, 1300 << 128, 3, 4]
    fee0, fee1, source = _live_v3_fees_from_growth(
        _Pool(), pos, current_tick=0, tick_lower=-100, tick_upper=100,
        liquidity=10, dec0=0, dec1=0,
    )
    assert source == "POOL_FEE_GROWTH"
    assert fee0 == pytest.approx(1003.0)
    assert fee1 == pytest.approx(2004.0)


def test_recent_range_fit_is_not_polluted_by_old_absolute_price_levels():
    rows = []
    for i in range(33 * 24):
        rows.append({"timestamp": i * 3600, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1})
    start = len(rows)
    for i in range(7 * 24):
        rows.append({"timestamp": (start + i) * 3600, "open": 200, "high": 202, "low": 198, "close": 200, "volume": 1})
    out = analyse_range(rows, 190, 210, horizon_days=7, candles_per_day=24)
    assert out["active_time_pct"] < 25
    assert out["average_horizon_activity_pct"] < 25
    assert out["recent_horizon_active_pct"] > 95
    assert out["recent_horizon_excursions"] == 0

    # Profit Lab must now use the recent requested horizon for CURRENT economics,
    # while older history remains available separately for walk-forward validation.
    decision = _decision_horizon_evidence(out, 7)
    assert decision["active_pct"] > 95
    assert decision["excursions"] == 0
    assert decision["interventions_per_month"] == pytest.approx(0.0)


def test_profit_lab_current_economics_has_no_direct_whole_history_activity_dependency():
    source = (Path(__file__).parents[1] / "lp_manager" / "profit_engine.py").read_text(encoding="utf-8")
    # One occurrence remains deliberately inside the compatibility fallback helper.
    assert source.count('analysis.get("average_horizon_activity_pct")') == 1
    assert 'active_time_pct=_f(decision_horizon.get("active_pct"))' in source
    assert 'active_scale=max(0.35,min(1.0,_f(decision_horizon.get("active_pct"))/100.0))' in source
    assert 'activity=max(0.45,min(1.15,_f(decision_horizon.get("active_pct"))/quick_active))' in source


def test_finalize_closed_position_freezes_accounting(tmp_path):
    store = Store(tmp_path / "closed.sqlite3")
    p = Position(
        id="closed:1", protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair="WETH/DELTA",
        status="CLOSED", lower_price=1, upper_price=2, current_price=0,
        capital_value=100, current_value=0, unclaimed_fees=0, fees_today=0, fees_7d=0,
        fees_30d=0, realised_fees=0, estimated_il=0, gas_costs=0, apr_current=0,
        apr_7d=0, opened_at=1_700_000_000, token_id="123", source="live_chain",
        lifecycle_stage="CLOSED", strategy_sleeve="TACTICAL_CAMPAIGN",
    )
    store.upsert_position(p)
    ok = store.finalize_closed_position(
        "closed:1", opening_capital_usd=100, total_fees_usd=12,
        gas_costs_usd=1, realised_pnl_usd=8, realised_return_pct=8,
        closed_at=1_700_100_000, quality="ONCHAIN_LIFECYCLE_FINAL",
    )
    assert ok is True
    row = store.get_position("closed:1")
    assert row["lifecycle_stage"] == "CLOSED_FINAL"
    assert row["capital_value"] == pytest.approx(100)
    assert row["realised_fees"] == pytest.approx(12)
    assert row["reported_net_pnl"] == pytest.approx(8)
    assert row["gas_costs"] == pytest.approx(1)


def test_profit_lab_ui_restores_goal_and_relevant_history_language():
    js = (Path(__file__).parents[1] / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert "Goal for this hold" in js
    assert "Recent range fit" in js
    assert "Historical fee cashflow" in js
    assert "Targets are reporting references, not instructions to increase risk." in js


class _FailingLogEth:
    def get_logs(self, _params):
        raise RuntimeError("provider rate limited")


class _FailingLogWeb3:
    eth = _FailingLogEth()


def test_closed_lifecycle_log_failures_are_not_treated_as_empty_history():
    manager = "0x" + "1" * 40
    with pytest.raises(RuntimeError, match="LIFECYCLE_LOG_SCAN_FAILED:INCREASE_LIQUIDITY:100-199"):
        _position_lifecycle_events(_FailingLogWeb3(), manager, 123, 100, 199, chunk=100)


class _ClosedFinaliserEth:
    block_number = 999

    def get_transaction_receipt(self, tx_hash):
        assert tx_hash == "0xopening"
        return {"blockNumber": 777}

    def contract(self, **_kwargs):
        return object()


class _ClosedFinaliserWeb3:
    eth = _ClosedFinaliserEth()


class _ClosedFinaliserCfg:
    key = "ROBINHOOD_CHAIN"

    def rpc_url(self):
        return "https://rpc.invalid"


class _ClosedFinaliserStore:
    def __init__(self):
        self.snapshot = {
            "pool_address": "0x" + "2" * 40,
            "token0": {"address": "0x" + "3" * 40, "symbol": "WETH", "decimals": 18},
            "token1": {"address": "0x" + "4" * 40, "symbol": "DELTA", "decimals": 18},
            "entry_evidence": {"transaction_hash": "0xopening"},
        }
        self.saved = None

    def list_positions(self, status):
        assert status == "CLOSED"
        return [{
            "id": "live:ROBINHOOD_CHAIN:123",
            "chain": "ROBINHOOD_CHAIN",
            "source": "live_chain",
            "lifecycle_stage": "CLOSED",
            "token_id": "123",
            "pool_address": self.snapshot["pool_address"],
        }]

    def get_position_snapshot(self, _position_id):
        return dict(self.snapshot)

    def save_position_snapshot(self, _position_id, snapshot):
        self.saved = dict(snapshot)
        self.snapshot = dict(snapshot)

    def finalize_closed_position(self, *_args, **_kwargs):
        raise AssertionError("test finaliser should stop before immutable final write")


def test_closed_finaliser_resolves_opening_block_before_lifecycle_scan(monkeypatch):
    store = _ClosedFinaliserStore()
    seen = {}
    monkeypatch.setattr(live_positions_module, "build_read_only_web3", lambda _url: _ClosedFinaliserWeb3())

    def fake_closed_final(**kwargs):
        seen["opening_block"] = kwargs["opening_block"]
        return {"complete": False, "reason": "TEST_STOP"}

    monkeypatch.setattr(live_positions_module, "_closed_position_final", fake_closed_final)
    result = finalise_closed_positions_from_store(store, _ClosedFinaliserCfg(), "0x" + "5" * 40, limit=1)

    assert result["attempted"] == 1
    assert result["partial"] == 1
    assert seen["opening_block"] == 777
    assert store.saved["opening_block_number"] == 777
    assert store.saved["entry_evidence"]["block_number"] == 777


def test_normal_refresh_never_runs_historical_closed_reconstruction():
    service_source = (Path(__file__).parents[1] / "lp_manager" / "live_service.py").read_text(encoding="utf-8")
    refresh_body = service_source.split("def refresh_positions", 1)[1].split("def import_opening_transactions", 1)[0]
    assert "finalise_closed_positions_from_store" not in refresh_body
    assert '"closed_history_mode":"MANUAL_ONLY"' in refresh_body


def test_background_loop_refreshes_current_positions_independent_of_optional_automation():
    service_source = (Path(__file__).parents[1] / "lp_manager" / "live_service.py").read_text(encoding="utf-8")
    worker_body = service_source.split("def worker():", 1)[1].split("self._thread = threading.Thread", 1)[0]
    assert "self.refresh_positions()" in worker_body
    assert 'if policy.get("market_monitoring"' not in worker_body
    assert '"live:background_health"' in worker_body


def test_historical_closed_nfts_have_operator_confirmed_opening_transactions():
    assert authoritative_opening_tx("ROBINHOOD_CHAIN", "1206967") == "0xd78b7cff1fe65d1b61ca77bc6b47ecbe8cc60a37dcdcbb1b89740367080ce55a"
    assert authoritative_opening_tx("ROBINHOOD_CHAIN", "1157839") == "0x4894f2748ce17a76810e46e4954a627a8e86ad72c2183d4fc978f7aee557a82c"
    assert authoritative_opening_tx("ROBINHOOD_CHAIN", "1206612") == "0xfd0c071eece65685b528300d9f7080024ddfa116242702d1d9f9ac3cf149266e"


class _LifecycleEth:
    def get_block(self, block):
        return {"timestamp": 1000 if int(block) == 100 else 2000}

    def get_transaction_receipt(self, _txh):
        return {"blockNumber": 200, "gasUsed": 0, "effectiveGasPrice": 0}

    def get_transaction(self, _txh):
        return {"from": "0x" + "9" * 40, "gasPrice": 0}


class _LifecycleWeb3:
    eth = _LifecycleEth()


def test_closed_lifecycle_records_exact_close_transaction(monkeypatch):
    events = [
        {"event_type": "INCREASE_LIQUIDITY", "block_number": 100, "log_index": 1,
         "transaction_hash": "0xopen", "liquidity": 100, "amount0_raw": 100, "amount1_raw": 0},
        {"event_type": "DECREASE_LIQUIDITY", "block_number": 200, "log_index": 1,
         "transaction_hash": "0xclose", "liquidity": 100, "amount0_raw": 110, "amount1_raw": 0},
        {"event_type": "COLLECT", "block_number": 200, "log_index": 2,
         "transaction_hash": "0xclose", "liquidity": 0, "amount0_raw": 112, "amount1_raw": 0},
    ]
    monkeypatch.setattr(live_positions_module, "_position_lifecycle_events", lambda *_a, **_k: [dict(x) for x in events])
    monkeypatch.setattr(
        live_positions_module, "_event_token_marks",
        lambda **_kwargs: (1.0, 1.0, ["TEST_USD"]),
    )

    class _Cfg:
        position_manager = "0x" + "1" * 40
        key = "ROBINHOOD_CHAIN"
        wrapped_native = ""

    final = _closed_position_final(
        w3=_LifecycleWeb3(), cfg=_Cfg(), wallet="0x" + "5" * 40,
        manager=None, pool_c=object(), pool="0x" + "2" * 40,
        token_id=123, opening_block=100, latest_block=200, market=None, market_row={},
        token0="0x" + "3" * 40, token1="0x" + "4" * 40,
        sym0="WETH", sym1="TEST", dec0=0, dec1=0,
        opening_entry={"transaction_hash": "0xopen"},
        current_unclaimed_usd=0.0, current_owed0=0.0, current_owed1=0.0,
    )

    assert final["complete"] is True
    assert final["close_transaction_hash"] == "0xclose"
    assert final["decrease_transaction_hashes"] == ["0xclose"]
    assert final["collect_transaction_hashes"] == ["0xclose"]
    assert final["lifecycle_transaction_hashes"] == ["0xopen", "0xclose"]
    assert final["closed_at"] == 2000
    assert final["total_fees_usd"] == pytest.approx(2.0)
    assert final["realised_pnl_usd"] == pytest.approx(12.0)


def test_reconcile_source_preserves_historical_and_opening_identity():
    source = (Path(__file__).parents[1] / "lp_manager" / "live_positions.py").read_text(encoding="utf-8")
    assert 'snap["historical_evidence"]=previous_snap.get("historical_evidence")' in source
    assert 'authoritative_opening_tx(result.chain,row.get("token_id"))' in source


def test_live_token_ids_excludes_closed_historical_positions(tmp_path):
    store = Store(tmp_path / "live_ids.sqlite3")
    common = dict(
        protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair="WETH/TEST",
        lower_price=1, upper_price=2, current_price=1.5,
        capital_value=100, current_value=100, unclaimed_fees=0,
        fees_today=0, fees_7d=0, fees_30d=0, realised_fees=0,
        estimated_il=0, gas_costs=0, apr_current=0, apr_7d=0,
        opened_at=1_700_000_000, source="live_chain",
        strategy_sleeve="TACTICAL_CAMPAIGN",
    )
    store.upsert_position(Position(
        id="live:ROBINHOOD_CHAIN:111", status="OPEN", token_id="111",
        lifecycle_stage="ACTIVE", **common,
    ))
    store.upsert_position(Position(
        id="live:ROBINHOOD_CHAIN:222", status="CLOSED", token_id="222",
        lifecycle_stage="CLOSED", **common,
    ))
    assert store.live_token_ids("ROBINHOOD_CHAIN") == {111}


def test_current_chain_scanner_cannot_launch_historical_closed_finaliser():
    source = (Path(__file__).parents[1] / "lp_manager" / "live_positions.py").read_text(encoding="utf-8")
    scan_body = source.split("def scan_chain_positions", 1)[1].split("def _next_live_display_name", 1)[0]
    assert "_closed_position_final(" not in scan_body
    assert '"history_mode":"MANUAL_ONLY"' in scan_body


def test_profit_lab_rejects_wrong_unit_history_that_is_only_numerically_near_spot():
    wrong_unit = [
        {"timestamp": 1_800_000_000 + i * 3600, "close": 2700.0}
        for i in range(24)
    ]
    correct_unit = [
        {"timestamp": 1_800_000_000 + i * 3600, "close": 4210.0}
        for i in range(24)
    ]
    assert _history_matches_spot(wrong_unit, 4263.23) is False
    assert _history_matches_spot(correct_unit, 4263.23) is True


def test_profit_lab_cache_requires_recent_history():
    now = 1_900_000_000.0
    fresh = [{"timestamp": now - 3600, "close": 100.0}]
    stale = [{"timestamp": now - 24 * 3600, "close": 100.0}]
    assert _history_cache_is_fresh(fresh, "hour", now=now) is True
    assert _history_cache_is_fresh(stale, "hour", now=now) is False


def test_profit_lab_v0811_does_not_reuse_pre_orientation_fix_history_cache():
    source = (Path(__file__).parents[1] / "lp_manager" / "profit_engine.py").read_text(encoding="utf-8")
    assert 'profit:history:v0811:' in source
    assert 'profit:history:v089:' not in source
    assert 'profit:history:v088:' not in source
    assert 'quote_symbol in stable_symbols' in source
    assert 'token-USD OHLC rejected for non-stable execution pair' in source


def test_profit_lab_alternative_range_rows_have_dedicated_readable_layout():
    root = Path(__file__).parents[1] / "lp_manager" / "static"
    js = (root / "app.js").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert 'profitAlternativeLabel' in js
    assert 'range-option profit-alt-row' in js
    assert 'profit-alt-kind' in js
    assert 'profit-alt-range' in js
    assert 'profit-alt-stat' in js
    assert '.profit-alt-row{' in css
    assert 'grid-template-columns:minmax(120px,150px)' in css


def test_profit_lab_regime_alignment_decides_between_near_equal_profit_ranges():
    rows = [
        {
            "skew_pct": -3.0,
            "profit_score": 72.0,
            "regime_alignment_score": 35.0,
            "forecast": {"expected_net_usd": 10.00},
        },
        {
            "skew_pct": 3.0,
            "profit_score": 90.0,
            "regime_alignment_score": 96.0,
            "forecast": {"expected_net_usd": 9.70},
        },
        {
            "skew_pct": 4.5,
            "profit_score": 99.0,
            "regime_alignment_score": 99.0,
            "forecast": {"expected_net_usd": 8.50},
        },
    ]
    best, policy = _select_regime_aware_best(
        rows,
        {"confidence": 80.0, "range_skew_pct": 3.2},
    )
    # 9.70 is inside the confidence-scaled near-best band and is therefore
    # preferred to the opposite-direction 10.00 candidate.
    assert best["skew_pct"] == pytest.approx(3.0)
    assert policy["max_expected_net_usd"] == pytest.approx(10.0)
    assert policy["selected_skew_pct"] == pytest.approx(3.0)
    assert policy["near_best_candidates"] == 2

    # A materially weaker economics case remains outside the band even if its
    # directional alignment score is excellent.
    assert best is not rows[2]


def test_profit_lab_exposes_regime_selection_audit_in_ui():
    js = (Path(__file__).parents[1] / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert "profitRegimeSelectionHtml" in js
    assert "Range direction audit:" in js
    assert "regime target skew" in js
    assert "selected skew" in js
    assert "near-best profit band" in js


def test_forecast_snapshot_links_to_discovered_position_and_is_queryable(tmp_path):
    store = Store(tmp_path / "forecast_link.sqlite3")
    p = Position(
        id="live:ROBINHOOD_CHAIN:999", protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair="WETH/TEST",
        status="OPEN", lower_price=90, upper_price=110, current_price=100,
        capital_value=100, current_value=100, unclaimed_fees=0, fees_today=0, fees_7d=0,
        fees_30d=0, realised_fees=0, estimated_il=0, gas_costs=0, apr_current=0,
        apr_7d=0, opened_at=1_700_000_000, token_id="999", source="live_chain",
        lifecycle_stage="ACTIVE", strategy_sleeve="TACTICAL_CAMPAIGN",
    )
    store.upsert_position(p)
    forecast = store.record_forecast_snapshot({
        "chain":"ROBINHOOD_CHAIN","pool_address":"0x"+"1"*40,"pair":"WETH/TEST",
        "sleeve":"TACTICAL_CAMPAIGN","horizon_days":7,"capital_usd":100,
        "spot":100,
        "recommended_range":{
            "lower":90,"upper":110,
            "forecast":{
                "expected_fees_usd":7,"expected_net_usd":6.5,
                "forecast_fee_apr_pct":365,"low_net_usd":3.5,"high_net_usd":9.0,
            },
        },
    })
    store.record_financial_event(
        position_id=None,event_type="OPEN_POSITION",chain="ROBINHOOD_CHAIN",
        tx_hash="0xabc",gas_usd=0.2,status="CONFIRMED",
        payload={"forecast_id":forecast["id"]},
    )

    linked = store.reconcile_execution_opening("0xabc", p.id)
    assert linked["linked_events"] == 1
    assert linked["linked_forecasts"] == 1

    actual = store.latest_forecast_for_position(p.id)
    assert actual is not None
    assert actual["id"] == forecast["id"]
    assert actual["position_id"] == p.id
    assert actual["model_version"] == "v0.8.11"
    assert actual["expected_fees_usd"] == pytest.approx(7.0)


def test_profit_forecast_path_uses_v0811_state_and_position_ui_exposes_actual_comparison():
    root = Path(__file__).parents[1]
    api_source = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'profit:last:v0811:' in api_source
    assert 'model_version="v0.8.11"' in api_source
    assert 'profit:last_recommendation:v0811' in api_source
    assert 'profit:last:v0810:' not in api_source
    assert 'model_version="v0.8.10"' not in api_source

    assert "execForecastId" in js
    assert "r.forecast_snapshot_id||null" in js
    assert "forecastId:execForecastId||null" in js
    assert "Forecast vs actual" in js
    assert "Expected fee pace now" in js
    assert "Actual tracked fees" in js
