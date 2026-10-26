"""Regression tests for safe, deterministic transaction-plan numeric validation.

No wallet, signing, RPC or broadcast is exercised here.
"""

import pytest

from lp_manager.transaction_plan import TransactionPlan, validate_transaction_plan


BASE_INTENT = {"lower_price": 100.0, "upper_price": 200.0, "capital_value": 50.0}


def _plan(action="OPEN", **changes):
    return TransactionPlan(
        action=action,
        chain="BASE",
        protocol="UNISWAP_V3",
        position_id="position-1" if action == "RECENTER" else None,
        intent={**BASE_INTENT, **changes},
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", "Infinity", "oops", None, True])
@pytest.mark.parametrize("field,expected_error", [
    ("lower_price", "INVALID_RANGE"),
    ("upper_price", "INVALID_RANGE"),
    ("capital_value", "INVALID_CAPITAL"),
])
def test_open_rejects_invalid_numbers_without_crashing(field, expected_error, value):
    result = validate_transaction_plan(_plan(**{field: value}))
    assert result["valid"] is False
    assert expected_error in result["errors"]


@pytest.mark.parametrize("field", ["lower_price", "upper_price"])
def test_recenter_rejects_nonfinite_range(field):
    result = validate_transaction_plan(_plan("RECENTER", **{field: float("nan")}))
    assert not result["valid"]
    assert "INVALID_RANGE" in result["errors"]


def test_valid_numeric_strings_remain_accepted():
    result = validate_transaction_plan(_plan(lower_price="100", upper_price="200", capital_value="50"))
    assert result["valid"]


def test_valid_recenter_does_not_require_open_capital():
    result = validate_transaction_plan(_plan("RECENTER", capital_value=None))
    assert result["valid"]


def test_dict_input_is_checked_too():
    result = validate_transaction_plan({
        "action": "OPEN", "chain": "BASE", "protocol": "UNISWAP_V3",
        "intent": {"lower_price": 100, "upper_price": float("inf"), "capital_value": 50},
    })
    assert not result["valid"]
    assert "INVALID_RANGE" in result["errors"]
