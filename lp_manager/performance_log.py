from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

from .financial_truth import portfolio_financial_truth
from .profit_dashboard import portfolio_profit_scorecard


EXTERNAL_DEPOSIT = "EXTERNAL_DEPOSIT"
EXTERNAL_WITHDRAWAL = "EXTERNAL_WITHDRAWAL"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _local_day_key(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(float(timestamp or time.time())).astimezone().date().isoformat()


def _local_day_window(day_key: str) -> tuple[float, float]:
    day = datetime.fromisoformat(str(day_key)).date()
    tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).timestamp()
    return start, start + 86400.0


def _portfolio_snapshot(store, sampled_at: float | None = None) -> dict[str, Any]:
    now = float(sampled_at or time.time())
    wallet = store.get_wallet_snapshot() or {}
    truth = portfolio_financial_truth(store)
    score = portfolio_profit_scorecard(store)

    liquid = _f(wallet.get("wallet_liquid_value_usd"))
    lp_value = _f(truth.get("current_open_lp_value_usd"))
    wealth = liquid + lp_value

    return {
        "sampled_at": now,
        "day_key": _local_day_key(now),
        "tracked_wealth_usd": round(wealth, 4),
        "liquid_wallet_usd": round(liquid, 4),
        "open_lp_value_usd": round(lp_value, 4),
        "all_time_fees_usd": round(_f(truth.get("all_time_known_fees_usd")), 4),
        "realised_gain_loss_usd": round(_f(truth.get("realised_gain_loss_usd")), 4),
        "transaction_costs_usd": round(_f(truth.get("transaction_costs_usd")), 4),
        "lp_vs_hodl_usd": truth.get("lp_vs_hodl_usd"),
        "fees_24h_usd": round(_f(score.get("fees_24h")), 4),
        "fees_7d_usd": round(_f(score.get("fees_7d")), 4),
        "fee_run_rate_month_usd": round(_f(score.get("fee_run_rate_month")), 4),
        "capital_earning_pct": round(_f(score.get("capital_currently_earning_pct")), 2),
        "open_positions": int(score.get("open_positions") or 0),
        "fee_quality": str(truth.get("all_time_fee_quality") or "UNKNOWN"),
        "positions": list(truth.get("position_rows") or []),
    }


def capture_performance_sample(store, sampled_at: float | None = None) -> dict[str, Any]:
    snapshot = _portfolio_snapshot(store, sampled_at)
    return store.upsert_performance_day(snapshot["day_key"], snapshot["sampled_at"], snapshot)


def _external_flows(events: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    net = 0.0
    rows = []
    for event in events:
        kind = str(event.get("event_type") or "").upper()
        if kind not in {EXTERNAL_DEPOSIT, EXTERNAL_WITHDRAWAL}:
            continue
        amount = max(0.0, _f(event.get("amount_usd")))
        signed = amount if kind == EXTERNAL_DEPOSIT else -amount
        net += signed
        rows.append({**event, "signed_amount_usd": round(signed, 4)})
    return round(net, 4), rows


def _day_timeline(store, day_key: str) -> list[dict[str, Any]]:
    start, end = _local_day_window(day_key)
    timeline: list[dict[str, Any]] = []

    for event in store.list_financial_events(2000):
        ts = _f(event.get("occurred_at"))
        if not (start <= ts < end):
            continue
        timeline.append({
            "timestamp": ts,
            "kind": "FINANCIAL_EVENT",
            "label": str(event.get("event_type") or "FINANCIAL EVENT").replace("_", " "),
            "status": str(event.get("status") or ""),
            "position_id": event.get("position_id"),
            "amount_usd": _f(event.get("amount_usd")),
            "gas_usd": _f(event.get("gas_usd")),
            "tx_hash": event.get("tx_hash"),
            "detail": str((event.get("payload") or {}).get("note") or ""),
        })

    for action in store.list_actions(2000):
        ts = _f(action.get("created_at"))
        if not (start <= ts < end):
            continue
        timeline.append({
            "timestamp": ts,
            "kind": "ACTION",
            "label": str(action.get("action_type") or "ACTION").replace("_", " "),
            "status": str(action.get("status") or ""),
            "position_id": action.get("position_id"),
            "amount_usd": 0.0,
            "gas_usd": 0.0,
            "tx_hash": "",
            "detail": str(action.get("mode") or ""),
        })

    for decision in store.list_decisions(2000):
        ts = _f(decision.get("created_at"))
        if not (start <= ts < end):
            continue
        timeline.append({
            "timestamp": ts,
            "kind": "DECISION",
            "label": str(decision.get("action") or "DECISION").replace("_", " "),
            "status": str(decision.get("severity") or ""),
            "position_id": decision.get("position_id"),
            "amount_usd": 0.0,
            "gas_usd": 0.0,
            "tx_hash": "",
            "detail": str(decision.get("summary") or ""),
        })

    timeline.sort(key=lambda row: row["timestamp"], reverse=True)
    return timeline[:150]


def _summarise_day(store, row: dict[str, Any], *, include_timeline: bool = False) -> dict[str, Any]:
    opening = dict(row.get("opening") or {})
    closing = dict(row.get("closing") or {})
    day_key = str(row.get("day_key") or opening.get("day_key") or closing.get("day_key") or "")
    start, end = _local_day_window(day_key)
    events = [e for e in store.list_financial_events(2000) if start <= _f(e.get("occurred_at")) < end]
    external_flow, flow_rows = _external_flows(events)

    opening_wealth = _f(opening.get("tracked_wealth_usd"))
    closing_wealth = _f(closing.get("tracked_wealth_usd"))
    portfolio_change = closing_wealth - opening_wealth
    performance_pnl = portfolio_change - external_flow
    fees_earned = _f(closing.get("all_time_fees_usd")) - _f(opening.get("all_time_fees_usd"))
    realised_delta = _f(closing.get("realised_gain_loss_usd")) - _f(opening.get("realised_gain_loss_usd"))
    cost_delta = _f(closing.get("transaction_costs_usd")) - _f(opening.get("transaction_costs_usd"))

    out = {
        "day_key": day_key,
        "first_sample_at": row.get("first_sample_at"),
        "last_sample_at": row.get("last_sample_at"),
        "samples": int(row.get("samples") or 0),
        "opening_wealth_usd": round(opening_wealth, 4),
        "closing_wealth_usd": round(closing_wealth, 4),
        "portfolio_change_usd": round(portfolio_change, 4),
        "external_cash_flow_usd": round(external_flow, 4),
        "performance_pnl_usd": round(performance_pnl, 4),
        "performance_return_pct": round(performance_pnl / opening_wealth * 100.0, 4) if opening_wealth > 0 else None,
        "fees_earned_usd": round(fees_earned, 4),
        "realised_gain_loss_delta_usd": round(realised_delta, 4),
        "transaction_cost_delta_usd": round(cost_delta, 4),
        "capital_earning_pct": closing.get("capital_earning_pct"),
        "fees_24h_usd": closing.get("fees_24h_usd"),
        "fee_run_rate_month_usd": closing.get("fee_run_rate_month_usd"),
        "open_positions": closing.get("open_positions"),
        "flow_records": flow_rows,
        "opening": opening,
        "closing": closing,
    }
    if include_timeline:
        out["timeline"] = _day_timeline(store, day_key)
    return out


def performance_log(store, *, capture: bool = False, limit: int = 120) -> dict[str, Any]:
    if capture:
        capture_performance_sample(store)

    rows = store.list_performance_days(limit)
    days = [_summarise_day(store, row, include_timeline=False) for row in rows]
    chronological = list(reversed(days))

    if chronological:
        first = chronological[0]
        latest = chronological[-1]
        start_wealth = _f(first.get("opening_wealth_usd"))
        current_wealth = _f(latest.get("closing_wealth_usd"))
        external = sum(_f(day.get("external_cash_flow_usd")) for day in chronological)
        performance_pnl = current_wealth - start_wealth - external
        fees = sum(_f(day.get("fees_earned_usd")) for day in chronological)
    else:
        start_wealth = current_wealth = external = performance_pnl = fees = 0.0

    current = dict((chronological[-1].get("closing") if chronological else {}) or {})
    return {
        "started_at": chronological[0].get("first_sample_at") if chronological else None,
        "logged_days": len(days),
        "running": {
            "start_wealth_usd": round(start_wealth, 4),
            "current_wealth_usd": round(current_wealth, 4),
            "net_external_cash_flow_usd": round(external, 4),
            "performance_pnl_usd": round(performance_pnl, 4),
            "performance_return_pct": round(performance_pnl / start_wealth * 100.0, 4) if start_wealth > 0 else None,
            "fees_earned_during_log_usd": round(fees, 4),
            "current_all_time_fees_usd": current.get("all_time_fees_usd"),
            "current_realised_gain_loss_usd": current.get("realised_gain_loss_usd"),
            "current_transaction_costs_usd": current.get("transaction_costs_usd"),
            "current_lp_vs_hodl_usd": current.get("lp_vs_hodl_usd"),
        },
        "days": days,
        "note": (
            "The Performance Log starts from the first v0.9.4 observation and does not invent earlier daily P/L. "
            "Performance P/L equals tracked-wealth change minus recorded external deposits/withdrawals. "
            "LP opens, closes, swaps and fee collections remain internal portfolio activity and are not treated as external cash flow."
        ),
    }


def performance_day(store, day_key: str) -> dict[str, Any] | None:
    row = store.get_performance_day(day_key)
    return _summarise_day(store, row, include_timeline=True) if row else None
