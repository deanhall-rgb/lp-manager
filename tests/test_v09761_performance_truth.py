from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from lp_manager.fee_metrics import calendar_fee_metrics, calendar_fee_window


def _today_bounds(now: float) -> tuple[float, float]:
    dt=datetime.fromtimestamp(now).astimezone()
    start=dt.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    return start, now


def test_restart_catchup_is_not_dumped_into_today():
    now=time.time()
    start,_=_today_bounds(now)
    old=start-14*86400
    tracker={
        "cumulative_earned_usd":12.60,
        "observations":[
            {"timestamp":old,"cumulative_earned_usd":10.00},
            {"timestamp":now,"cumulative_earned_usd":12.60},
        ],
    }
    cal=calendar_fee_metrics(tracker,now_ts=now)
    assert cal["today"]["actual_usd"] == 0.0
    assert cal["today"]["quality"].startswith("PARTIAL")
    assert cal["week"]["actual_usd"] == 0.0


def test_only_contiguous_post_restart_fee_delta_is_counted():
    now=time.time()
    start,_=_today_bounds(now)
    t1=max(start+60,now-600)
    t2=t1+300
    tracker={
        "cumulative_earned_usd":13.10,
        "observations":[
            {"timestamp":start-7*86400,"cumulative_earned_usd":10.00},
            {"timestamp":t1,"cumulative_earned_usd":12.60},
            {"timestamp":t2,"cumulative_earned_usd":13.10},
        ],
    }
    cal=calendar_fee_metrics(tracker,now_ts=max(now,t2+1))
    assert cal["today"]["actual_usd"] == 0.5
    assert cal["today"]["observed_seconds"] == 300.0


def test_long_gap_inside_month_is_unallocated_not_misattributed():
    now=time.time()
    dt=datetime.fromtimestamp(now).astimezone()
    month_start=dt.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()
    a=month_start+2*86400
    b=a+3*3600
    c=b+3*86400
    d=c+2*3600
    tracker={
        "observations":[
            {"timestamp":a,"cumulative_earned_usd":1.0},
            {"timestamp":b,"cumulative_earned_usd":2.0},
            {"timestamp":c,"cumulative_earned_usd":5.0},
            {"timestamp":d,"cumulative_earned_usd":5.5},
        ],
    }
    out=calendar_fee_window(tracker,month_start,now,now_ts=now)
    assert out["actual_usd"] == 1.5
    assert out["unallocated_gap_usd"] == 3.0
    assert out["gap_count"] == 1


def test_v09761_performance_refresh_path_is_lightweight():
    root=Path(__file__).resolve().parents[1]
    api=(root/"lp_manager"/"api.py").read_text(encoding="utf-8")
    app=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")
    live=(root/"lp_manager"/"live_service.py").read_text(encoding="utf-8")

    assert '"performance_log": build_performance_log(store, capture=False)' in api
    target_block=api.split('@app.post("/api/performance/target")',1)[1].split('@app.post("/api/performance/cash-flow")',1)[0]
    assert "build_performance_log" not in target_block
    calc_block=api.split('@app.post("/api/targets/calculate")',1)[1].split('@app.get("/api/positions")',1)[0]
    assert "portfolio_profit_scorecard" not in calc_block
    assert "performanceTargetBusy" in app
    assert "actual_today:Number(cf.today_usd||0)" in app
    assert "if(!silent||newCount>0||auto>0)" in app
    assert "capture_performance_sample(self.store)" in live
