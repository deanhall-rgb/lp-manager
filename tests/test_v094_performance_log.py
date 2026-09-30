from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from lp_manager.db import Store
from lp_manager.models import Position
from lp_manager.performance_log import (
    capture_performance_sample,
    performance_day,
    performance_log,
    set_performance_fee_target_pct,
    OFFICIAL_PERFORMANCE_START_DAY,
)


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
    assert day["fee_actual_usd"] == 2.0
    assert day["fee_target_usd"] is not None
    assert day["fee_target_usd"] > 0
    assert day["target_time_weighted_lp_capital_usd"] == 100.0
    assert day["fee_attainment_pct"] > 100

    detail=performance_day(store,day_key)
    assert detail is not None
    assert detail["performance_pnl_usd"] is None
    assert detail["historical_positions"][0]["quality"] == "PARTIAL"


def test_fee_target_is_movable_and_recalculates_same_evidence_window(tmp_path):
    store=Store(tmp_path/"target.sqlite3")
    position=_position(unclaimed=4.0,realised=0.0)
    position.opened_at=time.time()-3*86400
    store.upsert_position(position)

    target=time.time()-86400
    dt=datetime.fromtimestamp(target).astimezone().replace(hour=6,minute=0,second=0,microsecond=0)
    t1=dt.timestamp()
    t2=t1+12*3600
    store.set_setting("fees:tracker:"+position.id,{
        "opened_at":position.opened_at,
        "cumulative_earned_usd":4.0,
        "observations":[
            {"timestamp":t1,"cumulative_earned_usd":1.0},
            {"timestamp":t2,"cumulative_earned_usd":4.0},
        ],
    })

    set_performance_fee_target_pct(store,10.0)
    first=performance_log(store,capture=False)
    day_key=dt.date().isoformat()
    day10=next(x for x in first["days"] if x["day_key"]==day_key)
    target10=day10["fee_target_usd"]
    assert day10["fee_actual_usd"] == 3.0
    assert day10["target_window_hours"] == 12.0
    assert day10["target_time_weighted_lp_capital_usd"] == 100.0
    assert first["monthly_fee_target_pct"] == 10.0

    set_performance_fee_target_pct(store,20.0)
    second=performance_log(store,capture=False)
    day20=next(x for x in second["days"] if x["day_key"]==day_key)
    assert second["monthly_fee_target_pct"] == 20.0
    assert day20["fee_actual_usd"] == day10["fee_actual_usd"]
    assert abs(day20["fee_target_usd"] - target10*2) < 0.0002
    assert day20["fee_attainment_pct"] < day10["fee_attainment_pct"]


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


def test_official_performance_log_starts_24_september_without_deleting_underlying_evidence(tmp_path):
    store=Store(tmp_path/"official_start.sqlite3")
    tz=datetime.now().astimezone().tzinfo
    position=_position(unclaimed=6.0,realised=0.0)
    position.opened_at=datetime(2026,9,23,8,0,tzinfo=tz).timestamp()
    store.upsert_position(position)
    observations=[
        {"timestamp":datetime(2026,9,23,10,0,tzinfo=tz).timestamp(),"cumulative_earned_usd":1.0},
        {"timestamp":datetime(2026,9,23,20,0,tzinfo=tz).timestamp(),"cumulative_earned_usd":3.0},
        {"timestamp":datetime(2026,9,24,10,0,tzinfo=tz).timestamp(),"cumulative_earned_usd":4.0},
        {"timestamp":datetime(2026,9,24,20,0,tzinfo=tz).timestamp(),"cumulative_earned_usd":6.0},
    ]
    store.set_setting("fees:tracker:"+position.id,{
        "opened_at":position.opened_at,
        "cumulative_earned_usd":6.0,
        "observations":observations,
    })

    log=performance_log(store,capture=False)
    assert OFFICIAL_PERFORMANCE_START_DAY == "2026-09-24"
    assert log["official_start_day"] == "2026-09-24"
    assert "2026-09-24" in [x["day_key"] for x in log["days"]]
    assert "2026-09-23" not in [x["day_key"] for x in log["days"]]
    # Underlying tracker evidence is preserved; it is only excluded from the official view.
    tracker=store.get_setting("fees:tracker:"+position.id,{})
    assert len(tracker["observations"]) == 4
    assert performance_day(store,"2026-09-23") is None


def test_v094_final_cleanup_keeps_replay_as_advanced_validation_only():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="replay"' not in html
    assert 'id="replay-section"' in html
    assert 'id="system-replay-btn"' in html
    assert "Open Replay validation" in html
    assert "systemReplay.onclick=()=>navigate('replay')" in js


def test_v094_final_cleanup_positions_exposes_only_safe_lp_refresh():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'id="position-rescan-btn"' in html
    assert "Refresh LPs" in html
    assert 'id="position-tx-import-btn"' not in html
    assert 'id="delta-history-btn"' not in html
    assert 'id="legacy-import-btn"' not in html
    assert 'id="new-position-btn-2"' not in html
    assert 'id="delta-history-file"' not in html
    assert "positionRescan.onclick=()=>rescanWalletLPs()" in js


def test_v094_ui_has_separate_performance_log_workspace():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="performance"' in html
    assert 'id="performance-section"' in html
    assert 'id="performance-cashflow-btn"' in html
    assert 'id="performance-target-rate"' in html
    assert 'id="performance-target-btn"' in html
    assert 'id="performance-fee-summary"' in html
    assert "v0.9.6.3 · portfolio-aware advisor" in html
    assert "Daily fee performance" in html
    assert "Accounting & audit detail" in html
    assert "function renderPerformance()" in js
    assert "function updatePerformanceTarget" in js
    assert "Fees earned" in js
    assert "Fee target" in js
    assert "Target attainment" in js
    assert "time-weighted committed LP" in js
    assert "data-performance-day" in js
    assert "$('[data-performance-day]').forEach" in js
    assert "Hourly checkpoints" in js
    assert "Record external cash flow" in js
    assert "official_start_day" not in js or "Official LP fee-performance reporting starts" in js
