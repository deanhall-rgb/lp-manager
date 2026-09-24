import json
from lp_manager.db import Store
from lp_manager.legacy_bridge import import_campaign_ledger


def campaign(ts, token_id, lower, upper, current):
    return {
        "opened_at_unix": ts,
        "capital_usd": 500,
        "position_snapshot": {
            "token0_symbol":"DELTA","token1_symbol":"WETH","token_id":token_id,
            "lower_price":lower,"upper_price":upper,"current_price":current,"position_value_usd":520,
            "chain":"ROBINHOOD_CHAIN","protocol":"UNISWAP_V3",
        },
        "economics":{"unclaimed_fees_usd":20},
    }


def test_delta_campaigns_get_stable_lp_labels(tmp_path):
    payload={"campaigns":{"later":campaign(300,3,14,22,16),"first":campaign(100,1,9,13,12),"middle":campaign(200,2,11,15,14)}}
    (tmp_path/"campaign_ledger.json").write_text(json.dumps(payload))
    store=Store(tmp_path/"db.sqlite3")
    out=import_campaign_ledger(store,tmp_path)
    assert out["imported"] == 3
    rows=sorted(store.list_positions(),key=lambda r:r["opened_at"])
    assert [r["display_name"] for r in rows] == ["DELTA LP1","DELTA LP2","DELTA LP3"]
    assert all(r["cost_basis_quality"] == "LEGACY_LEDGER" for r in rows)
