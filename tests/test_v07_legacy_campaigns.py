import json
from pathlib import Path
from lp_manager.db import Store
from lp_manager.legacy_bridge import import_campaign_ledger
from lp_manager.analytics import position_economics


def campaign(cid, opened, status, tick0=-70000, tick1=-65000, current_tick=-67500, pnl=12.5):
    return {
        "schema_version":3,"campaign_id":cid,"status":status,"updated_at_unix":opened+3600,
        "entry":{"time_unix":opened,"entry_nav_usd":100.0,"basis_quality":"EXACT_ONCHAIN_ASSETS","entry_gas_usd":0.25,"pool_address":"0x1111111111111111111111111111111111111111"},
        "position_snapshot":{
            "protocol":"UNISWAP_V3","pool_address":"0x1111111111111111111111111111111111111111","ids":[opened],
            "token0_symbol":"DELTA","token1_symbol":"WETH","token0_decimals":18,"token1_decimals":18,
            "tick_lower":tick0,"tick_upper":tick1,"current_tick":current_tick,"human_price_asset":"DELTA","human_quote_asset":"WETH",
            "principal_usd":95,"fees_usd":4,"total_usd":104,"fee_return_pct":4,
            "token0_address":"0x2222222222222222222222222222222222222222","token1_address":"0x3333333333333333333333333333333333333333",
        },
        "current":{"principal_usd":95,"unclaimed_fees_usd":4,"total_lp_value_usd":104,"net_pnl_usd":pnl,"absolute_pnl_pct":pnl,"absolute_pnl_status":"READY","lp_vs_hodl_net_usd":2.0},
    }


def test_v3_campaign_ledger_imports_open_and_closed_delta(tmp_path: Path):
    root=tmp_path/"legacy"; root.mkdir()
    payload={"schema_version":3,"campaigns":{
        "a":campaign("a",1000,"LP_LEG_CLOSED_UNSETTLED",pnl=5),
        "b":campaign("b",2000,"LP_LEG_CLOSED_UNSETTLED",pnl=10),
        "c":campaign("c",3000,"OPEN_LP",pnl=15),
    }}
    (root/"campaign_ledger.json").write_text(json.dumps(payload))
    store=Store(tmp_path/"db.sqlite")
    result=import_campaign_ledger(store,root)
    assert result["imported"] == 3
    assert result["skipped"] == 0
    rows=store.list_positions()
    assert [r["display_name"] for r in sorted(rows,key=lambda r:r["opened_at"])] == ["DELTA LP1","DELTA LP2","DELTA LP3"]
    assert len(store.list_positions("CLOSED")) == 3
    assert len(store.list_positions("OPEN")) == 0
    # Historical ledgers do not prove current ownership; only a live chain scan may reopen a campaign.
    assert all(r["lower_price"] > 0 and r["upper_price"] > r["lower_price"] for r in rows)
    assert rows[0]["range_unit"] == "DELTA_PER_WETH"
    econ=position_economics(store.get_position("legacy-a"))
    assert econ["net_profit"] == 5
    snap=store.get_position_snapshot("legacy-a")
    assert snap["current"]["lp_vs_hodl_net_usd"] == 2.0
