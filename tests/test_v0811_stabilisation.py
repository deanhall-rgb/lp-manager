from __future__ import annotations

from pathlib import Path

import pytest

from lp_manager.db import Store
from lp_manager.live_positions import _live_v3_fees_from_growth
from lp_manager.models import Position
from lp_manager.range_lab import analyse_range


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
    assert out["recent_horizon_active_pct"] > 95
    assert out["recent_horizon_excursions"] == 0


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
