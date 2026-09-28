from __future__ import annotations

import time
from datetime import datetime
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


def test_pre_v094_fee_tracker_days_are_backfilled_without_inventing_pnl(tmp_path):
    store=Store(tmp_path/"history.sqlite3")
    position=_position(unclaimed=3.0,realised=0.0)
    position.opened_at=time.time()-4*86400
    store.upsert_position(position)

    target=time.time()-2*86400
    dt=datetime.fromtimestamp(target).astimezone().replace(hour=12,minute=0,second=0,microsecond=0)
    t1=dt.timestamp()
    t2=t1+6*3600
    store.set_setting("fees:tracker:"+position.id,{
        "opened_at":position.opened_at,
        "cumulative_earned_usd":3.0,
        "observations":[
            {"timestamp":t1,"cumulative_earned_usd":1.0},
            {"timestamp":t2,"cumulative_earned_usd":3.0},
        ],
    })
    store.record_action(
        position_id=position.id,action_type="REVIEW_POSITION",mode="READ_ONLY",
        status="RECORDED",payload={"note":"history evidence"},
    )

    log=performance_log(store,capture=False)
    day_key=dt.date().isoformat()
    day=next(x for x in log["days"] if x["day_key"]==day_key)
    assert day["evidence_quality"] == "PARTIAL"
    assert day["performance_pnl_usd"] is None
    assert day["opening_wealth_usd"] is None
    assert day["fees_earned_usd"] == 2.0
    assert day["historical_positions"][0]["fees_earned_usd"] == 2.0

    detail=performance_day(store,day_key)
    assert detail is not None
    assert detail["performance_pnl_usd"] is None
    assert detail["historical_positions"][0]["quality"] == "PARTIAL"


def test_v094_capture_retains_hourly_checkpoints(tmp_path):
    store=Store(tmp_path/"hourly.sqlite3")
    store.save_wallet_snapshot({"wallet_liquid_value_usd":1000.0})
    base=datetime.now().astimezone().replace(minute=5,second=0,microsecond=0).timestamp()
    capture_performance_sample(store,base)
    capture_performance_sample(store,base+60)

    day_key=datetime.fromtimestamp(base).astimezone().date().isoformat()
    detail=performance_day(store,day_key)
    assert detail is not None
    assert detail["evidence_quality"] == "RECORDED"
    assert len(detail["hourly"]) == 1
    assert detail["hourly"][0]["samples"] == 2


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
    assert "Evidence boundary:" in js
    assert "Historical fee evidence" in js
    assert "Not captured" in js
    assert "Hourly checkpoints" in js
    assert "Record external cash flow" in js
