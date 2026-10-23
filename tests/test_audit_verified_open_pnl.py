"""Regression coverage for mixed verified/unverified opening capital.

The aggregate gross open P/L must use the same subset of positions for both
current strategy wealth and opening capital. Total current wealth is separate.
"""

from __future__ import annotations

import pytest

from lp_manager.financial_truth import portfolio_financial_truth


class _Store:
    def __init__(self, verified: frozenset[str]) -> None:
        self.verified = verified

    def list_positions(self):
        rows = [
            ("verified", 100.0, 115.0),
            ("unverified", 200.0, 250.0),
        ]
        return [
            {
                "id": pid,
                "status": "OPEN",
                "capital_value": capital,
                "current_value": current,
                "unclaimed_fees": 0.0,
                "realised_fees": 0.0,
                "gas_costs": 0.0,
                "cost_basis_quality": (
                    "VERIFIED_OPENING_BASIS" if pid in self.verified else "UNKNOWN"
                ),
            }
            for pid, capital, current in rows
        ]

    def get_position_snapshot(self, position_id):
        return {}

    def get_setting(self, key, default=None):
        return default

    def list_financial_events(self, limit):
        return []

    def list_forecast_snapshots(self, limit):
        return []


@pytest.mark.parametrize(
    ("verified", "expected_pnl", "expected_opening"),
    [
        (frozenset({"verified"}), 15.0, 100.0),
        (frozenset({"verified", "unverified"}), 65.0, 300.0),
        (frozenset(), None, 0.0),
    ],
)
def test_aggregate_open_pnl_uses_only_positions_with_verified_basis(
    verified, expected_pnl, expected_opening
):
    result = portfolio_financial_truth(_Store(verified))
    assert result["current_open_strategy_wealth_usd"] == pytest.approx(365.0)
    assert result["current_open_lp_value_usd"] == pytest.approx(365.0)
    assert result["opening_wealth_verified_usd"] == pytest.approx(expected_opening)
    assert result["basis_coverage"] == {
        "verified_open_positions": len(verified),
        "open_positions": 2,
    }
    if expected_pnl is None:
        assert result["gross_open_pnl_incl_fees_usd"] is None
    else:
        assert result["gross_open_pnl_incl_fees_usd"] == pytest.approx(expected_pnl)
