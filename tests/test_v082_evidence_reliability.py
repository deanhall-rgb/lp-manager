import json
from pathlib import Path

from lp_manager.analytics import range_metrics
from lp_manager.db import Store
from lp_manager.historical_import import import_delta_pool_history


def _bundled():
    return json.loads((Path(__file__).parents[1] / "lp_manager" / "reference_data" / "delta_history_summary.json").read_text())


def test_delta_today_106408_is_below_all_three_historical_ranges():
    payload=_bundled()
    for row in payload["positions"].values():
        lo=(1.0001 ** int(row["tick_lower"]))
        hi=(1.0001 ** int(row["tick_upper"]))
        r=range_metrics({"lower_price":lo,"upper_price":hi,"current_price":106408.0})
        assert r["range_state"] == "OUT_BELOW"


def test_bundled_delta_financial_calibration_is_not_zeroed(tmp_path: Path):
    store=Store(tmp_path/"db.sqlite")
    import_delta_pool_history(store,_bundled())
    rows={r["display_name"]:r for r in store.list_positions()}
    assert rows["P2 - WETH/DELTA"]["reported_net_pnl"] == 81.00
    assert rows["DELTA LP2"]["reported_net_pnl_pct"] == 36.75
    assert rows["P2 - WETH/DELTA"]["realised_fees"] == 46.16
    assert rows["P3 - WETH/DELTA"]["reported_net_pnl"] == 14.11
    assert rows["DELTA LP3"]["reported_net_pnl_pct"] == 10.73
    assert rows["P3 - WETH/DELTA"]["realised_fees"] == 4.70
    assert rows["DELTA LP1"]["reported_net_pnl"] == 0  # no final close settlement invented
    for row in rows.values():
        assert row["current_price"] == 0


def test_exact_fee_claim_evidence_for_p2_p3_survives_import(tmp_path: Path):
    store=Store(tmp_path/"db.sqlite")
    import_delta_pool_history(store,_bundled())
    p2=store.get_position_snapshot("history:ROBINHOOD_CHAIN:1209940")
    p3=store.get_position_snapshot("history:ROBINHOOD_CHAIN:1252512")
    assert abs(p2["financial_evidence"]["fee_claim_weth"]-0.007502804329083346) < 1e-15
    assert p2["financial_evidence"]["fee_claim_delta"] > 1382
    assert abs(p3["financial_evidence"]["fee_claim_weth"]-0.000834971083454201) < 1e-15
    assert p3["financial_evidence"]["fee_claim_delta"] > 130
