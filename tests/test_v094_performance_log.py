from __future__ import annotations

import time
from pathlib import Path

from lp_manager.db import Store
from lp_manager.models import Position
from lp_manager.performance_log import capture_performance_sample, performance_day, performance_log


def _position(*, unclaimed: float, realised: float) -> Position:
    return Position(
        id="live:ROBINHOOD_CHAIN:999",protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",
        pair="WETH/TEST",status="OPEN",lower_price=90.0,upper_price=110.0,current_price=100.0,
        capital_value=100.0,current_value=100.0,unclaimed_fees=unclaimed,
        fees_today=0.0,fees_7d=0.0,fees_30d=0.0,realised_fees=realised,
        estimated_il=0.0,gas_costs=0.0,apr_current=0.0,apr_7d=0.0,
        opened_at=time.time()-86400,token_id="999",source="live_chain",
        display_name="P99 · WETH/TEST",pool_address="0xtest",
        cost_basis_quality="VERIFIED_OPENING_BASIS",
    )


def test_external_cash_flow_is_removed_from_daily_performance(tmp_path):
    store=Store(tmp_path/"performance.sqlite3")
    now=time.time()
    store.save_wallet_snapshot({"wallet_liquid_value_usd":1000.0})
    capture_performance_sample(store,now)

    store.record_financial_event(
        position_id=None,campaign_id=None,event_type="EXTERNAL_DEPOSIT",
        chain="PORTFOLIO",amount_usd=100.0,occurred_at=now+10,
        payload={"note":"test deposit"},
    )
    store.save_wallet_snapshot({"wallet_liquid_value_usd":1100.0})
    capture_performance_sample(store,now+20)

    log=performance_log(store,capture=False)
    assert log["logged_days"] == 1
    day=log["days"][0]
    assert day["portfolio_change_usd"] == 100.0
    assert day["external_cash_flow_usd"] == 100.0
    assert day["performance_pnl_usd"] == 0.0
    assert log["running"]["performance_pnl_usd"] == 0.0

    detail=performance_day(store,day["day_key"])
    assert detail is not None
    assert any(x["label"]=="EXTERNAL DEPOSIT" for x in detail["timeline"])


def test_fee_collection_does_not_create_fake_portfolio_profit(tmp_path):
    store=Store(tmp_path/"collection.sqlite3")
    now=time.time()
    store.upsert_position(_position(unclaimed=10.0,realised=0.0))
    store.save_wallet_snapshot({"wallet_liquid_value_usd":900.0})
    capture_performance_sample(store,now)

    # Collecting the same £/$10-equivalent fee moves value from unclaimed LP fees
    # into the liquid wallet. Accounting wealth should remain unchanged.
    store.upsert_position(_position(unclaimed=0.0,realised=10.0))
    store.save_wallet_snapshot({"wallet_liquid_value_usd":910.0})
    capture_performance_sample(store,now+20)

    day=performance_log(store,capture=False)["days"][0]
    assert day["opening_wealth_usd"] == 1010.0
    assert day["closing_wealth_usd"] == 1010.0
    assert day["performance_pnl_usd"] == 0.0


def test_v094_ui_has_separate_performance_log_workspace():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="performance"' in html
    assert 'id="performance-section"' in html
    assert 'id="performance-cashflow-btn"' in html
    assert "v0.9.4 · performance log" in html
    assert "function renderPerformance()" in js
    assert "Accounting wealth" in js
    assert "Monthly fee run-rate" in js
    assert "data-performance-day" in js
    assert "$('[data-performance-day]').forEach" in js
    assert " $('[data-performance-day]').forEach" not in js
    assert "Record external cash flow" in js
