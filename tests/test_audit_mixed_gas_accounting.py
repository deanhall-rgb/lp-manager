from __future__ import annotations

import pytest

from lp_manager.financial_truth import portfolio_financial_truth


class _Store:
    def __init__(self, positions, events):
        self.positions = positions
        self.events = events

    def list_positions(self):
        return self.positions

    def get_position_snapshot(self, position_id):
        return {}

    def get_setting(self, key, default=None):
        return default

    def list_financial_events(self, limit):
        return self.events

    def list_forecast_snapshots(self, limit):
        return []


def _position(pid, gas, status="OPEN"):
    return {
        "id": pid, "status": status, "source": "live_chain",
        "gas_costs": gas, "realised_fees": 0.0, "unclaimed_fees": 0.0,
    }


def _event(pid, gas, status="CONFIRMED"):
    return {"position_id": pid, "gas_usd": gas, "status": status, "event_type": "OPEN_POSITION"}


@pytest.fixture(autouse=True)
def _isolate_accounting(monkeypatch):
    # This suite tests cost-evidence reconciliation, not LP valuation.
    monkeypatch.setattr(
        "lp_manager.financial_truth.position_accounting",
        lambda position, snapshot, tracker: {
            "basis_ready": False,
            "current_principal_usd": 0.0,
            "strategy_wealth_usd": 0.0,
        },
    )


def test_mixed_receipt_and_imported_gas_is_not_lost():
    # The 0.20 receipt is already included in the 0.80 recorded position total.
    # The second position has no receipt, and the 0.15 wallet-level cost is
    # unlinked. Previously any receipt made the entire total only 0.35.
    store = _Store(
        [_position("receipt", 0.80), _position("legacy", 0.40)],
        [_event("receipt", 0.20), _event(None, 0.15)],
    )
    out = portfolio_financial_truth(store)
    assert out["transaction_costs_usd"] == pytest.approx(1.35)
    assert out["realised_gain_loss_usd"] == pytest.approx(-1.20)


def test_receipt_cost_higher_than_recorded_position_cost_is_used_once():
    store = _Store(
        [_position("receipt", 0.10)],
        [_event("receipt", 0.40), _event("receipt", 5.0, status="PENDING")],
    )
    out = portfolio_financial_truth(store)
    assert out["transaction_costs_usd"] == pytest.approx(0.40)
    assert out["realised_gain_loss_usd"] == pytest.approx(-0.40)


def test_position_costs_are_preserved_when_no_receipts_exist():
    store = _Store([_position("open", 0.30), _position("closed", 0.70, status="CLOSED")], [])
    out = portfolio_financial_truth(store)
    assert out["transaction_costs_usd"] == pytest.approx(1.00)
    assert out["realised_gain_loss_usd"] == pytest.approx(-0.30)


def test_confirmed_unlinked_wallet_costs_are_not_discarded():
    store = _Store([], [_event(None, 0.45), _event("not-discovered", 0.20)])
    out = portfolio_financial_truth(store)
    assert out["transaction_costs_usd"] == pytest.approx(0.65)
