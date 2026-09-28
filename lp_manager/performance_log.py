from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Any

from .financial_truth import portfolio_financial_truth
from .profit_dashboard import portfolio_profit_scorecard


EXTERNAL_DEPOSIT = "EXTERNAL_DEPOSIT"
EXTERNAL_WITHDRAWAL = "EXTERNAL_WITHDRAWAL"
TRACKER_PREFIX = "fees:tracker:"
HISTORICAL_LOOKBACK_DAYS = 35


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _local_day_key(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(float(timestamp or time.time())).astimezone().date().isoformat()


def _local_hour_key(timestamp: float | None = None) -> str:
    dt = datetime.fromtimestamp(float(timestamp or time.time())).astimezone()
    return dt.strftime("%Y-%m-%dT%H:00%z")


def _local_day_window(day_key: str) -> tuple[float, float]:
    day = datetime.fromisoformat(str(day_key)).date()
    tz = datetime.now().astimezone().tzinfo
    start_dt = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end_dt = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start_dt.timestamp(), end_dt.timestamp()


def _portfolio_snapshot(store, sampled_at: float | None = None) -> dict[str, Any]:
    now = float(sampled_at or time.time())
    wallet = store.get_wallet_snapshot() or {}
    truth = portfolio_financial_truth(store)
    score = portfolio_profit_scorecard(store)

    liquid = _f(wallet.get("wallet_liquid_value_usd"))
    lp_value = _f(truth.get("current_open_lp_value_usd"))
    canonical_open = [
        p for p in store.list_positions()
        if str(p.get("status") or "").upper() == "OPEN"
        and str(p.get("monitoring_class") or "").upper() != "ARCHIVED_SUPERSEDED"
        and str(p.get("source") or "") != "legacy_campaign_ledger"
    ]
    unclaimed = sum(max(0.0, _f(p.get("unclaimed_fees"))) for p in canonical_open)
    # Accounting wealth includes currently unclaimed LP fees, but not cumulative
    # collected fees again because collections already sit in the wallet.
    wealth = liquid + lp_value + unclaimed

    return {
        "sampled_at": now,
        "day_key": _local_day_key(now),
        "tracked_wealth_usd": round(wealth, 4),
        "liquid_wallet_usd": round(liquid, 4),
        "open_lp_value_usd": round(lp_value, 4),
        "unclaimed_fees_usd": round(unclaimed, 4),
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
    day = store.upsert_performance_day(snapshot["day_key"], snapshot["sampled_at"], snapshot)
    store.upsert_performance_hour(
        _local_hour_key(snapshot["sampled_at"]),
        snapshot["day_key"],
        snapshot["sampled_at"],
        snapshot,
    )
    return day


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


def _position_map(store) -> dict[str, dict[str, Any]]:
    return {str(p.get("id")): p for p in store.list_positions()}


def _tracker_rows(store) -> list[dict[str, Any]]:
    rows = []
    for setting in store.list_settings_prefix(TRACKER_PREFIX):
        key = str(setting.get("key") or "")
        tracker = setting.get("value") or {}
        if not isinstance(tracker, dict):
            continue
        observations = sorted(
            [
                {
                    "timestamp": _f(x.get("timestamp")),
                    "cumulative_earned_usd": max(0.0, _f(x.get("cumulative_earned_usd"))),
                }
                for x in (tracker.get("observations") or [])
                if _f(x.get("timestamp")) > 0
            ],
            key=lambda x: x["timestamp"],
        )
        if not observations:
            continue
        rows.append({
            "position_id": key[len(TRACKER_PREFIX):],
            "tracker": tracker,
            "observations": observations,
        })
    return rows


def _fee_evidence_for_day(store, day_key: str) -> tuple[float, list[dict[str, Any]], list[float]]:
    start, end = _local_day_window(day_key)
    positions = _position_map(store)
    detail: list[dict[str, Any]] = []
    evidence_times: list[float] = []
    total = 0.0

    for item in _tracker_rows(store):
        observations = item["observations"]
        if observations[-1]["timestamp"] < start or observations[0]["timestamp"] >= end:
            continue
        pid = str(item["position_id"])
        tracker = item["tracker"]
        opened = max(0.0, _f(tracker.get("opened_at")))
        before_start = [x for x in observations if x["timestamp"] <= start]
        in_day = [x for x in observations if start <= x["timestamp"] < end]
        before_end = [x for x in observations if x["timestamp"] < end]
        if not before_end:
            continue

        last = before_end[-1]
        if before_start:
            first = before_start[-1]
            base_cumulative = first["cumulative_earned_usd"]
            coverage_start = first["timestamp"]
        elif opened and start <= opened < end:
            base_cumulative = 0.0
            coverage_start = opened
        elif in_day:
            first = in_day[0]
            base_cumulative = first["cumulative_earned_usd"]
            coverage_start = first["timestamp"]
        else:
            continue

        earned = max(0.0, last["cumulative_earned_usd"] - base_cumulative)
        # A tracker point within two hours of each boundary is strong enough to
        # describe the calendar day as recorded. Otherwise the delta is still
        # genuine observed evidence but only covers part of the day.
        start_ok = abs(coverage_start - start) <= 2 * 3600 or (opened and start <= opened < end)
        end_ok = (end - last["timestamp"]) <= 2 * 3600
        quality = "RECORDED" if start_ok and end_ok else "PARTIAL"

        position = positions.get(pid) or {}
        label = str(position.get("display_name") or position.get("pair") or pid)
        row = {
            "position_id": pid,
            "display_name": label,
            "pair": str(position.get("pair") or ""),
            "fees_earned_usd": round(earned, 4),
            "cumulative_start_usd": round(base_cumulative, 4),
            "cumulative_end_usd": round(last["cumulative_earned_usd"], 4),
            "coverage_start": coverage_start,
            "coverage_end": last["timestamp"],
            "observation_count": len(in_day),
            "quality": quality,
        }
        detail.append(row)
        total += earned
        evidence_times.extend([coverage_start, last["timestamp"]])

    detail.sort(key=lambda x: x["fees_earned_usd"], reverse=True)
    return round(total, 4), detail, evidence_times


def _day_timeline(store, day_key: str) -> list[dict[str, Any]]:
    start, end = _local_day_window(day_key)
    timeline: list[dict[str, Any]] = []

    for event in store.list_financial_events(5000):
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
            "evidence_quality": "RECORDED",
        })

    for action in store.list_actions(5000):
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
            "evidence_quality": "RECORDED",
        })

    for decision in store.list_decisions(5000):
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
            "evidence_quality": "RECORDED",
        })

    for forecast in store.list_forecast_snapshots(5000):
        ts = _f(forecast.get("created_at"))
        if not (start <= ts < end):
            continue
        timeline.append({
            "timestamp": ts,
            "kind": "FORECAST",
            "label": "PROFIT LAB FORECAST",
            "status": str(forecast.get("model_version") or ""),
            "position_id": forecast.get("position_id"),
            "amount_usd": _f(forecast.get("expected_net_usd")),
            "gas_usd": 0.0,
            "tx_hash": "",
            "detail": (
                f"{forecast.get('pair') or ''} · {forecast.get('horizon_days') or 0:g}d · "
                f"forecast fee APR {_f(forecast.get('forecast_fee_apr_pct')):.1f}%"
            ),
            "evidence_quality": "RECORDED",
        })

    # Position lifecycle timestamps are useful historical evidence even where the
    # browser workflow was not yet recording a financial event.
    for position in store.list_positions():
        opened = _f(position.get("opened_at"))
        closed = _f(position.get("closed_at"))
        if start <= opened < end:
            timeline.append({
                "timestamp": opened,
                "kind": "POSITION_LIFECYCLE",
                "label": "POSITION OPENED",
                "status": str(position.get("cost_basis_quality") or ""),
                "position_id": position.get("id"),
                "amount_usd": _f(position.get("capital_value")),
                "gas_usd": 0.0,
                "tx_hash": "",
                "detail": str(position.get("display_name") or position.get("pair") or ""),
                "evidence_quality": "RECORDED",
            })
        if closed and start <= closed < end:
            timeline.append({
                "timestamp": closed,
                "kind": "POSITION_LIFECYCLE",
                "label": "POSITION CLOSED",
                "status": str(position.get("pnl_quality") or ""),
                "position_id": position.get("id"),
                "amount_usd": _f(position.get("reported_net_pnl")),
                "gas_usd": _f(position.get("gas_costs")),
                "tx_hash": "",
                "detail": str(position.get("display_name") or position.get("pair") or ""),
                "evidence_quality": "RECORDED",
            })

    timeline.sort(key=lambda row: row["timestamp"], reverse=True)
    return timeline[:250]


def _historical_day(store, day_key: str, *, include_timeline: bool = False) -> dict[str, Any] | None:
    start, end = _local_day_window(day_key)
    fees, fee_positions, fee_times = _fee_evidence_for_day(store, day_key)
    timeline = _day_timeline(store, day_key)
    evidence_times = list(fee_times) + [_f(x.get("timestamp")) for x in timeline if _f(x.get("timestamp")) > 0]
    if not evidence_times and not fee_positions:
        return None

    return {
        "day_key": day_key,
        "first_sample_at": min(evidence_times) if evidence_times else start,
        "last_sample_at": max(evidence_times) if evidence_times else start,
        "samples": sum(int(x.get("observation_count") or 0) for x in fee_positions),
        "opening_wealth_usd": None,
        "closing_wealth_usd": None,
        "portfolio_change_usd": None,
        "external_cash_flow_usd": round(_external_flows([
            e for e in store.list_financial_events(5000)
            if start <= _f(e.get("occurred_at")) < end
        ])[0], 4),
        "performance_pnl_usd": None,
        "performance_return_pct": None,
        "fees_earned_usd": fees,
        "realised_gain_loss_delta_usd": None,
        "transaction_cost_delta_usd": round(sum(
            max(0.0, _f(e.get("gas_usd")))
            for e in store.list_financial_events(5000)
            if start <= _f(e.get("occurred_at")) < end
        ), 4),
        "capital_earning_pct": None,
        "fees_24h_usd": None,
        "fee_run_rate_month_usd": None,
        "open_positions": None,
        "flow_records": [],
        "opening": {},
        "closing": {},
        "evidence_quality": "PARTIAL",
        "evidence_note": (
            "Pre-v0.9.4 day reconstructed only from evidence already stored at the time. "
            "Fee deltas, lifecycle records, forecasts, actions and decisions are shown where recorded; "
            "whole-portfolio wealth/P&L was not historically snapshotted and is therefore not invented."
        ),
        "historical_positions": fee_positions,
        "hourly": [],
        **({"timeline": timeline} if include_timeline else {}),
    }


def _summarise_day(store, row: dict[str, Any], *, include_timeline: bool = False) -> dict[str, Any]:
    opening = dict(row.get("opening") or {})
    closing = dict(row.get("closing") or {})
    day_key = str(row.get("day_key") or opening.get("day_key") or closing.get("day_key") or "")
    start, end = _local_day_window(day_key)
    events = [e for e in store.list_financial_events(5000) if start <= _f(e.get("occurred_at")) < end]
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
        "evidence_quality": "RECORDED",
        "evidence_note": (
            "Direct v0.9.4 accounting snapshot window. P/L is the observed accounting-wealth change "
            "less recorded external cash flow."
        ),
        "historical_positions": [],
        "hourly": store.list_performance_hours(day_key) if include_timeline else [],
    }
    if include_timeline:
        out["timeline"] = _day_timeline(store, day_key)
    return out


def _evidence_day_keys(store, lookback_days: int = HISTORICAL_LOOKBACK_DAYS) -> set[str]:
    cutoff = time.time() - max(1, int(lookback_days)) * 86400.0
    keys: set[str] = set()

    for item in _tracker_rows(store):
        for obs in item["observations"]:
            ts = _f(obs.get("timestamp"))
            if ts >= cutoff:
                keys.add(_local_day_key(ts))

    for event in store.list_financial_events(5000):
        ts = _f(event.get("occurred_at"))
        if ts >= cutoff:
            keys.add(_local_day_key(ts))
    for action in store.list_actions(5000):
        ts = _f(action.get("created_at"))
        if ts >= cutoff:
            keys.add(_local_day_key(ts))
    for decision in store.list_decisions(5000):
        ts = _f(decision.get("created_at"))
        if ts >= cutoff:
            keys.add(_local_day_key(ts))
    for forecast in store.list_forecast_snapshots(5000):
        ts = _f(forecast.get("created_at"))
        if ts >= cutoff:
            keys.add(_local_day_key(ts))
    for position in store.list_positions():
        for ts in (_f(position.get("opened_at")), _f(position.get("closed_at"))):
            if ts >= cutoff:
                keys.add(_local_day_key(ts))
    return keys


def performance_log(store, *, capture: bool = False, limit: int = 120) -> dict[str, Any]:
    if capture:
        capture_performance_sample(store)

    recorded_rows = store.list_performance_days(limit)
    recorded = {
        str(row.get("day_key")): _summarise_day(store, row, include_timeline=False)
        for row in recorded_rows
    }

    all_days = dict(recorded)
    for day_key in _evidence_day_keys(store):
        if day_key in all_days:
            continue
        historical = _historical_day(store, day_key, include_timeline=False)
        if historical:
            all_days[day_key] = historical

    days = sorted(all_days.values(), key=lambda row: str(row.get("day_key") or ""), reverse=True)[:max(1, int(limit))]
    chronological_recorded = sorted(recorded.values(), key=lambda row: str(row.get("day_key") or ""))

    if chronological_recorded:
        first = chronological_recorded[0]
        latest = chronological_recorded[-1]
        start_wealth = _f(first.get("opening_wealth_usd"))
        current_wealth = _f(latest.get("closing_wealth_usd"))
        external = sum(_f(day.get("external_cash_flow_usd")) for day in chronological_recorded)
        performance_pnl = current_wealth - start_wealth - external
    else:
        start_wealth = current_wealth = external = performance_pnl = 0.0

    fees = sum(_f(day.get("fees_earned_usd")) for day in days)
    current = dict((chronological_recorded[-1].get("closing") if chronological_recorded else {}) or {})
    partial_days = sum(1 for day in days if day.get("evidence_quality") != "RECORDED")

    return {
        "started_at": min((_f(day.get("first_sample_at")) for day in days if _f(day.get("first_sample_at")) > 0), default=None),
        "recorded_started_at": chronological_recorded[0].get("first_sample_at") if chronological_recorded else None,
        "logged_days": len(days),
        "recorded_days": len(recorded),
        "historical_evidence_days": partial_days,
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
            "current_fees_24h_usd": current.get("fees_24h_usd"),
            "current_fee_run_rate_month_usd": current.get("fee_run_rate_month_usd"),
            "current_capital_earning_pct": current.get("capital_earning_pct"),
            "current_unclaimed_fees_usd": current.get("unclaimed_fees_usd"),
        },
        "days": days,
        "note": (
            "Direct portfolio P/L begins with the first v0.9.4 accounting snapshot. Earlier dates are backfilled only "
            "from stored evidence such as fee-tracker observations, lifecycle events, forecasts, actions and decisions; "
            "historical whole-portfolio P/L is shown as Not captured rather than invented. From v0.9.4 onward the system "
            "also keeps hourly accounting checkpoints. Accounting wealth is liquid wallet + open LP principal + currently "
            "unclaimed fees, so collecting fees does not create fake profit."
        ),
    }


def performance_day(store, day_key: str) -> dict[str, Any] | None:
    row = store.get_performance_day(day_key)
    if row:
        return _summarise_day(store, row, include_timeline=True)
    return _historical_day(store, day_key, include_timeline=True)
