from __future__ import annotations

import math

import pytest

from lp_manager.execution_pricing import complete_pool_usd_marks
from lp_manager.live_v3_builder import scale_quote_to_capital


def _meta(current=None):
    return {
        "token0": {"address": "0xaaa", "symbol": "FOO"},
        "token1": {"address": "0xbbb", "symbol": "BAR"},
        "price_lens": {"unit": "FOO_PER_BAR", "current": current},
    }


def test_valid_ratio_completion_remains_available():
    result = complete_pool_usd_marks(_meta(100), {"0xaaa": 2.0})
    assert result["anchored"] is True
    assert result["price0_usd"] == pytest.approx(2.0)
    assert result["price1_usd"] == pytest.approx(200.0)
    assert result["sources"]["BAR"] == "POOL_RATIO_FROM_FOO"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True, "invalid"])
def test_invalid_provider_mark_never_certifies_capital_quote(invalid):
    result = complete_pool_usd_marks(_meta(None), {"0xaaa": invalid, "0xbbb": 3.0})
    assert result["anchored"] is False
    assert result["price0_usd"] == 0.0
    assert result["sources"]["FOO"] == "MISSING"
    assert all(math.isfinite(value) for value in result["marks"].values())


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), True])
def test_invalid_pool_ratio_never_derives_usd_price(invalid):
    result = complete_pool_usd_marks(_meta(invalid), {"0xaaa": 2.0})
    assert result["anchored"] is False
    assert result["price1_usd"] == 0.0


def test_overflowing_ratio_multiplication_is_not_accepted():
    result = complete_pool_usd_marks(_meta(1e308), {"0xaaa": 1e308})
    assert result["anchored"] is False
    assert result["price1_usd"] == 0.0


def test_overflowing_ratio_division_is_not_accepted():
    result = complete_pool_usd_marks(_meta(1e-308), {"0xbbb": 1e308})
    assert result["anchored"] is False
    assert result["price0_usd"] == 0.0


def test_valid_capital_sizing_preserves_budget_and_ratio():
    a0, a1 = scale_quote_to_capital(
        amount0=1.0, amount1=0.5, price0_usd=2.0, price1_usd=4.0, capital_usd=100.0
    )
    assert a0 == pytest.approx(25.0)
    assert a1 == pytest.approx(12.5)
    assert a0 * 2.0 + a1 * 4.0 == pytest.approx(100.0)


def test_single_sided_quote_does_not_require_unused_side_price():
    a0, a1 = scale_quote_to_capital(
        amount0=1.0, amount1=0.0, price0_usd=2.0, price1_usd=0.0, capital_usd=100.0
    )
    assert (a0, a1) == pytest.approx((50.0, 0.0))


@pytest.mark.parametrize("field,invalid", [
    ("amount0", float("nan")), ("amount1", float("inf")),
    ("price0_usd", float("inf")), ("price1_usd", -float("inf")),
    ("capital_usd", float("nan")), ("capital_usd", float("inf")),
    ("amount0", -1.0), ("price0_usd", -2.0), ("capital_usd", 0.0),
    ("amount0", True), ("price1_usd", False), ("capital_usd", "bad"),
])
def test_invalid_capital_sizing_inputs_fail_closed(field, invalid):
    values = dict(amount0=1.0, amount1=0.5, price0_usd=2.0, price1_usd=4.0, capital_usd=100.0)
    values[field] = invalid
    with pytest.raises(ValueError, match="cannot size"):
        scale_quote_to_capital(**values)


def test_positive_amount_without_price_fails_closed():
    with pytest.raises(ValueError, match="cannot size"):
        scale_quote_to_capital(
            amount0=1.0, amount1=0.0, price0_usd=0.0, price1_usd=0.0, capital_usd=100.0
        )


def test_overflowing_unit_value_and_scale_fail_closed():
    with pytest.raises(ValueError, match="cannot size"):
        scale_quote_to_capital(
            amount0=1e308, amount1=1e308, price0_usd=1e308, price1_usd=1e308, capital_usd=100.0
        )
    with pytest.raises(ValueError, match="cannot size"):
        scale_quote_to_capital(
            amount0=1.0, amount1=0.0, price0_usd=1e-308, price1_usd=0.0, capital_usd=1e308
        )
