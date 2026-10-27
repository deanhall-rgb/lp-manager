"""Fail-closed canonical opportunity numeric parsing; no providers or wallets used."""

import json
import math

import pytest

from lp_manager.opportunity_model import canonical_opportunity, freshness_state


@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", "-Infinity", True, False,
])
@pytest.mark.parametrize("field", ["tvl_usd", "volume_24h_usd"])
def test_nonfinite_or_boolean_market_metrics_do_not_escape_to_canonical_data(field, bad):
    row = canonical_opportunity({
        "chain": "BASE",
        "pool_address": "0x" + "a" * 40,
        "protocol": "UNISWAP_V3",
        field: bad,
    }, now=1000)
    assert row[field] == 0.0
    assert math.isfinite(row[field])
    # The public API must be able to serialize canonical metrics as strict JSON.
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "-Infinity", "nan", True])
def test_invalid_provider_timestamps_have_unknown_freshness(bad):
    row = canonical_opportunity({
        "source_updated_at": bad,
        "analysed_at": bad,
    }, now=1000)
    assert row["freshness"]["market"] == "UNKNOWN"
    assert row["freshness"]["analysis"] == "UNKNOWN"
    assert row["freshness"]["market_updated_at"] is None
    assert row["freshness"]["analysed_at"] is None
    assert row["source_updated_at"] is None
    assert row["analysed_at"] is None
    json.dumps(row, allow_nan=False)
    assert freshness_state(bad, now=1000) == ("UNKNOWN", None)


def test_finite_numeric_strings_and_valid_freshness_remain_supported():
    row = canonical_opportunity({
        "tvl_usd": "123456.5",
        "volume_24h_usd": "2500",
        "source_updated_at": "900",
        "analysed_at": "850",
    }, now=1000)
    assert row["tvl_usd"] == 123456.5
    assert row["volume_24h_usd"] == 2500.0
    assert row["freshness"]["market"] == "FRESH"
    assert row["freshness"]["analysis"] == "FRESH"


def test_invalid_legacy_fallback_timestamp_fields_do_not_leak_nonfinite_values():
    row = canonical_opportunity({
        "market_updated_at": float("inf"),
        "analysis_updated_at": float("nan"),
    }, now=1000)
    assert row["market_updated_at"] is None
    assert row["analysis_updated_at"] is None
    assert row["freshness"]["market"] == "UNKNOWN"
    assert row["freshness"]["analysis"] == "UNKNOWN"
    json.dumps(row, allow_nan=False)
