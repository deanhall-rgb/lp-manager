from __future__ import annotations

import json
from pathlib import Path

import pytest

from lp_manager.closed_history import import_closed_position_finals
from lp_manager.db import Store
from lp_manager.financial_truth import portfolio_financial_truth
from lp_manager.models import Position


def _position(
    *,
    pid: str,
    token_id: str,
    pair: str,
    status: str = "CLOSED",
    source: str = "live_chain",
    monitoring_class: str = "HISTORICAL",
) -> Position:
    return Position(
        id=pid,
        protocol="UNISWAP_V3",
        chain="ROBINHOOD_CHAIN",
        pair=pair,
        status=status,
        lower_price=1,
        upper_price=2,
        current_price=1.5 if status == "OPEN" else 0,
        capital_value=100,
        current_value=100 if status == "OPEN" else 0,
        unclaimed_fees=0,
        fees_today=0,
        fees_7d=0,
        fees_30d=0,
        realised_fees=0,
        estimated_il=0,
        gas_costs=0,
        apr_current=0,
        apr_7d=0,
        opened_at=1_700_000_000,
        token_id=token_id,
        source=source,
        monitoring_class=monitoring_class,
        display_name=pair,
        lifecycle_stage="ACTIVE" if status == "OPEN" else "CLOSED",
        strategy_sleeve="TACTICAL_CAMPAIGN",
    )


def test_evidence_pack_freezes_all_five_closed_positions(tmp_path):
    store = Store(tmp_path / "closed_finals.sqlite3")
    payload = json.loads(
        (Path(__file__).parents[1] / "lp_manager" / "reference_data" / "closed_position_finals_v0811.json")
        .read_text(encoding="utf-8")
    )

    for item in payload["positions"]:
        store.upsert_position(
            _position(
                pid=f"live:ROBINHOOD_CHAIN:{item['token_id']}",
                token_id=str(item["token_id"]),
                pair=str(item["pair"]),
            )
        )

    result = import_closed_position_finals(store, payload)

    assert result["ok"] is True
    assert result["finalised"] == 5
    assert result["missing"] == []

    expected_fees = sum(float(x["total_fees_usd"]) for x in payload["positions"])

    for item in payload["positions"]:
        row = store.find_position_by_token("ROBINHOOD_CHAIN", str(item["token_id"]))
        assert row is not None
        assert row["status"] == "CLOSED"
        assert row["lifecycle_stage"] == "CLOSED_FINAL"
        assert row["display_name"] == item["display_name"]
        assert row["realised_fees"] == pytest.approx(float(item["total_fees_usd"]))
        assert row["reported_net_pnl"] == pytest.approx(float(item["realised_pnl_usd"]))
        assert row["gas_costs"] == pytest.approx(float(item["gas_costs_usd"]))

        snap = store.get_position_snapshot(str(row["id"])) or {}
        final = snap.get("closed_final") or {}
        assert final["complete"] is True
        assert final["close_transaction_hash"] == item["close_transaction_hash"]
        assert final["fee_transaction_hash"] == item["fee_transaction_hash"]
        assert final["liquidity_settled"] is True

    truth = portfolio_financial_truth(store)
    assert truth["all_time_known_fees_usd"] == pytest.approx(round(expected_fees, 2))
    assert truth["all_time_fee_quality"] == "COMPLETE"
    assert len(truth["all_time_fee_breakdown"]) == 5


def test_all_time_fees_exclude_legacy_and_archived_rows(tmp_path):
    store = Store(tmp_path / "canonical_truth.sqlite3")

    canonical = _position(
        pid="live:ROBINHOOD_CHAIN:1",
        token_id="1",
        pair="WETH/TEST",
        status="OPEN",
        source="live_chain",
        monitoring_class="ACTIVE",
    )
    legacy = _position(
        pid="legacy:delta",
        token_id="2",
        pair="WETH/DELTA",
        status="OPEN",
        source="legacy_campaign_ledger",
        monitoring_class="ACTIVE",
    )
    archived = _position(
        pid="old:superseded",
        token_id="3",
        pair="WETH/DELTA",
        status="OPEN",
        source="manual",
        monitoring_class="ARCHIVED_SUPERSEDED",
    )

    for row in (canonical, legacy, archived):
        store.upsert_position(row)

    store.set_setting("fees:tracker:live:ROBINHOOD_CHAIN:1", {
        "cumulative_earned_usd": 12.5,
        "fees_24h_usd": 1.0,
        "age_days": 2.0,
    })
    store.set_setting("fees:tracker:legacy:delta", {
        "cumulative_earned_usd": 999.0,
        "fees_24h_usd": 999.0,
        "age_days": 2.0,
    })
    store.set_setting("fees:tracker:old:superseded", {
        "cumulative_earned_usd": 888.0,
        "fees_24h_usd": 888.0,
        "age_days": 2.0,
    })

    truth = portfolio_financial_truth(store)

    assert truth["all_time_known_fees_usd"] == pytest.approx(12.5)
    assert [x["position_id"] for x in truth["all_time_fee_breakdown"]] == [
        "live:ROBINHOOD_CHAIN:1"
    ]
