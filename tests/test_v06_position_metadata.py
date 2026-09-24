from lp_manager.db import Store
from lp_manager.models import Position


def position():
    return Position(id="p",protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",pair="DELTA/WETH",status="OPEN",lower_price=10,upper_price=20,current_price=15,capital_value=500,current_value=520,unclaimed_fees=12,fees_today=3,fees_7d=10,fees_30d=10,realised_fees=0,estimated_il=0,gas_costs=0,apr_current=80,apr_7d=70,opened_at=1)


def test_position_product_metadata_round_trip(tmp_path):
    store=Store(tmp_path/"db.sqlite3")
    store.upsert_position(position())
    row=store.update_position_metadata("p",display_name="DELTA LP1",entry_thesis="Bullish continuation",exit_goal="Bank campaign profit")
    assert row["display_name"] == "DELTA LP1"
    assert row["entry_thesis"] == "Bullish continuation"
    assert row["exit_goal"] == "Bank campaign profit"
