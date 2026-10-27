"""Wallet cash/equity evidence must fail closed without live wallet access."""

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from lp_manager.db import Store
from lp_manager import wallet_audit


def _settings():
    return SimpleNamespace(
        wallet_address="0x1111111111111111111111111111111111111111",
        currency="USD",
    )


def _reviewed_store(tmp_path):
    store = Store(tmp_path / "wallet_audit.sqlite3")
    store.upsert_wallet_audit_event({
        "id": "fund",
        "chain": "BASE",
        "tx_hash": "0xknown",
        "transfer_key": "fund",
        "occurred_at": 1_790_000_000.0,
        "direction": "IN",
        "category": "external",
        "asset": "ETH",
        "amount": 1.0,
        "source": "TEST",
        "payload": {},
    })
    wallet_audit.resolve_wallet_audit_event(
        _settings(), store, "fund",
        classification="CONFIRMED_FUNDING",
        fiat_amount=1000.0,
    )
    store.set_setting("wallet_audit:last_scan", {
        "coverage_complete": True,
        "chains": [{"chain": "BASE", "transfers": 1, "transactions": 1,
                    "full_transaction_history": True}],
    })
    return store


@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", "-Infinity", True, False,
])
def test_nonfinite_or_boolean_cash_inputs_cannot_be_confirmed(tmp_path, bad):
    store = Store(tmp_path / "cash.sqlite3")
    store.upsert_wallet_audit_event({
        "id": "fund", "chain": "BASE", "tx_hash": "0xabc",
        "transfer_key": "fund", "occurred_at": 1_790_000_000.0,
        "direction": "IN", "category": "external", "asset": "ETH",
        "amount": 1.0, "source": "TEST", "payload": {},
    })
    with pytest.raises(ValueError, match="real cash amount"):
        wallet_audit.resolve_wallet_audit_event(
            _settings(), store, "fund",
            classification="CONFIRMED_FUNDING", fiat_amount=bad,
        )
    assert store.get_wallet_audit_event("fund")["review_status"] == "UNRESOLVED"


@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", None, True, -1.0,
])
def test_invalid_wallet_equity_does_not_create_false_audited_loss(tmp_path, bad):
    store = _reviewed_store(tmp_path)
    store.save_wallet_snapshot({"total_tracked_value_usd": bad})
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["confirmed_contributions"] == 1000.0
    assert metrics["valuation_complete"] is False
    assert metrics["pnl_complete"] is False
    assert metrics["current_portfolio_value"] is None
    assert metrics["provisional_true_pnl"] is None
    json.dumps(metrics, allow_nan=False)


def test_missing_wallet_snapshot_is_unavailable_not_zero(tmp_path):
    store = _reviewed_store(tmp_path)
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["valuation_complete"] is False
    assert metrics["provisional_true_pnl"] is None


def test_valid_zero_and_numeric_string_equity_remain_distinguishable(tmp_path):
    store = _reviewed_store(tmp_path)
    store.save_wallet_snapshot({"total_tracked_value_usd": "950.0"})
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["valuation_complete"] is True
    assert metrics["pnl_complete"] is True
    assert metrics["current_portfolio_value"] == 950.0
    assert metrics["provisional_true_pnl"] == -50.0

    store.save_wallet_snapshot({"total_tracked_value_usd": 0.0})
    zero = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert zero["valuation_complete"] is True
    assert zero["current_portfolio_value"] == 0.0
    assert zero["provisional_true_pnl"] == -1000.0


def test_numeric_sanitizer_blocks_nonfinite_provider_costs():
    for bad in (float("nan"), float("inf"), "-Infinity", True):
        assert wallet_audit._f(bad) == 0.0
    assert wallet_audit._f("0.000042") == pytest.approx(0.000042)
    assert math.isfinite(wallet_audit._f(float("inf")))


def test_wallet_audit_ui_displays_unavailable_equity_instead_of_zero():
    js = (Path(__file__).resolve().parents[1] / "lp_manager" / "static" / "app.js").read_text()
    assert "const equityReady=" in js
    assert "const pnlReady=" in js
    assert "UNAVAILABLE · wallet valuation missing or invalid" in js
