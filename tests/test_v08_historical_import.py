from pathlib import Path

from lp_manager.db import Store
from lp_manager.historical_import import import_delta_pool_history
from lp_manager.models import Position


def _payload(status="closed"):
    return {
        "metadata": {
            "chain_id": 4663,
            "pool": "0xD64FbdA67E1015dF43Fa5e49F02cA844729E5F94",
            "token0": {"address": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73", "symbol": "WETH", "decimals": 18},
            "token1": {"address": "0xe8ffd7e24187F72afB08d75B1bb13088A989a791", "symbol": "DELTA", "decimals": 18},
            "fee_pips": 10000,
            "latest_chain_time_at_run_utc": "2026-09-21T19:00:24Z",
        },
        "positions": {
            "P1": {
                "token_id": 1206967,
                "open_timestamp": 1789665115,
                "close_timestamp": 1790005318 if status == "closed" else None,
                "tick_lower": 119000,
                "tick_upper": 124000,
                "opening_tick_reconstructed": 120888,
                "opening_delta_per_weth_reconstructed": 177761.06574873225,
                "actual_initial_weth": 0.051698967550025725,
                "actual_initial_delta": 5750.0,
                "status": status,
                "elapsed_seconds": 352109,
                "range_utilisation_pct": 99.662,
                "active_hours": 97.4775,
                "inactive_hours": 0.3306,
                "exit_count": 4,
                "reentry_count": 3,
                "swap_count_during_lifetime": 10719,
                "fee_est_weth_from_non_crossing_active_swaps": 0.019448845847996717,
            }
        },
    }


def test_delta_reconstruction_uses_delta_per_weth_and_is_historical_not_live(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite")
    result = import_delta_pool_history(store, _payload(status="open"))
    assert result["imported"] == 1
    row = store.list_positions()[0]
    assert row["status"] == "CLOSED"  # snapshot-open is not current ownership proof
    assert row["display_name"] == "P1 - WETH/DELTA"
    assert row["campaign_label"] == "P1"
    assert row["range_unit"] == "DELTA_PER_WETH"
    assert 140_000 < row["lower_price"] < 160_000
    assert row["current_price"] == 0  # entry is not allowed to masquerade as today's pool price
    assert 230_000 < row["upper_price"] < 250_000
    snap = store.get_position_snapshot(row["id"])
    assert snap["fee_evidence"]["reconstructed_weth_lower_bound"] > 0.019
    assert snap["observed"]["range_utilisation_pct"] > 99


def test_live_chain_record_keeps_current_authority_when_history_is_imported(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite")
    live = Position(
        id="live:ROBINHOOD_CHAIN:1206967", protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN",
        pair="WETH/DELTA", status="OPEN", lower_price=150000, upper_price=250000,
        current_price=210000, capital_value=100, current_value=111, unclaimed_fees=7,
        fees_today=0, fees_7d=0, fees_30d=0, realised_fees=0, estimated_il=0, gas_costs=0,
        apr_current=0, apr_7d=0, opened_at=1789665115, token_id="1206967", source="live_chain",
        range_unit="DELTA_PER_WETH",
    )
    store.upsert_position(live)
    store.save_position_snapshot(live.id, {"live": True, "token_id": "1206967"})
    import_delta_pool_history(store, _payload(status="closed"))
    row = store.get_position(live.id)
    assert row["status"] == "OPEN"
    assert row["current_value"] == 111
    snap = store.get_position_snapshot(live.id)
    assert snap["historical_evidence"]["historical_pool_reconstruction"] is True
