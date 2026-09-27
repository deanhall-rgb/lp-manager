from __future__ import annotations

from lp_manager.campaign_accounting import build_campaigns, campaign_identity
from lp_manager.db import Store
from lp_manager.models import Position


def _position(pid, pair, status, opened, current_value=100.0, capital=100.0, reported=0.0, quality="UNKNOWN"):
    return Position(
        id=pid,
        protocol="UNISWAP_V3",
        chain="ROBINHOOD_CHAIN",
        pair=pair,
        status=status,
        lower_price=1,
        upper_price=2,
        current_price=1.5,
        capital_value=capital,
        current_value=current_value,
        unclaimed_fees=0,
        fees_today=0,
        fees_7d=0,
        fees_30d=0,
        realised_fees=0,
        estimated_il=0,
        gas_costs=0,
        apr_current=0,
        apr_7d=0,
        opened_at=opened,
        token_id=pid.split(":")[-1],
        source="live_chain",
        display_name=pid,
        lifecycle_stage="CLOSED_FINAL" if status=="CLOSED" else "ACTIVE",
        cost_basis_quality="ONCHAIN_MINT_RECONSTRUCTED",
        reported_net_pnl=reported,
        pnl_quality=quality,
    )


def _live_snapshot(asset_symbol, asset_address, asset_amount=100.0, asset_price=1.0, quote_symbol="WETH", quote_address="0xbbb"):
    return {
        "token0":{
            "symbol":asset_symbol,
            "address":asset_address,
            "amount":asset_amount,
            "price_usd":asset_price,
        },
        "token1":{
            "symbol":quote_symbol,
            "address":quote_address,
            "amount":0.02,
            "price_usd":2500.0,
        },
        "entry_evidence":{
            "basis_complete":True,
            "entry_value_usd":100.0,
            "token0_amount":asset_amount,
            "token1_amount":0.02,
            "token0_price_usd":0.5,
            "token1_price_usd":2500.0,
            "opened_at":1000,
            "transaction_hash":"0xopen",
            "quality":"ONCHAIN_MINT_RECONSTRUCTED",
        },
    }


def test_campaign_identity_groups_asset_across_quote_pairs():
    delta=campaign_identity({"chain":"ROBINHOOD_CHAIN","pair":"WETH/DELTA"}, {})
    pons_eth=campaign_identity({"chain":"ROBINHOOD_CHAIN","pair":"WETH/PONS"}, {})
    pons_usdg=campaign_identity({"chain":"ROBINHOOD_CHAIN","pair":"PONS/USDG"}, {})
    weth_income=campaign_identity({"chain":"ROBINHOOD_CHAIN","pair":"WETH/USDG"}, {})

    assert delta["id"] == "campaign:ROBINHOOD_CHAIN:DELTA"
    assert pons_eth["id"] == pons_usdg["id"] == "campaign:ROBINHOOD_CHAIN:PONS"
    assert weth_income["id"] == "campaign:ROBINHOOD_CHAIN:WETH_INCOME"


def test_campaign_aggregates_child_results_without_counting_unattributed_wallet_inventory(tmp_path):
    store=Store(tmp_path/"campaigns.sqlite3")

    closed=_position(
        "live:ROBINHOOD_CHAIN:1","WETH/DELTA","CLOSED",1000,
        current_value=0,reported=10.0,quality="ONCHAIN_EXECUTION_RECEIPT_FINAL"
    )
    open_pos=_position(
        "live:ROBINHOOD_CHAIN:2","WETH/DELTA","OPEN",2000,
        current_value=95.0,capital=100.0
    )
    store.upsert_position(closed)
    store.upsert_position(open_pos)

    closed_snap=_live_snapshot("DELTA","0xaaa",100,0.6)
    closed_snap["closed_final"]={
        "complete":True,
        "quality":"ONCHAIN_EXECUTION_RECEIPT_FINAL",
        "opening_capital_usd":100.0,
        "total_fees_usd":5.0,
        "transaction_costs_usd":1.0,
        "gas_usd":1.0,
        "realised_pnl_usd":10.0,
        "closed_at":1500,
        "close_transaction_hash":"0xclose",
    }
    store.save_position_snapshot(closed.id,closed_snap)

    open_snap=_live_snapshot("DELTA","0xaaa",100,0.45)
    open_snap["current_value_usd"]=95.0
    open_snap["unclaimed_fees_usd"]=3.0
    store.save_position_snapshot(open_pos.id,open_snap)
    store.set_setting(f"fees:tracker:{open_pos.id}",{"cumulative_earned_usd":3.0})

    # Open leg: 95 current principal + 3 fees - 1 gas - 100 basis = -3.
    with store.connect() as con:
        con.execute("UPDATE positions SET gas_costs=1 WHERE id=?",(open_pos.id,))

    store.save_wallet_snapshot({
        "holdings":[{
            "chain":"ROBINHOOD_CHAIN","symbol":"DELTA","address":"0xaaa",
            "balance":50.0,"price_usd":0.5,"value_usd":25.0,
            "data_quality":"LIVE_BALANCE_AND_PRICE","discovery_source":"ALCHEMY",
        }],
        "hidden_holdings":[],
    })

    campaigns=build_campaigns(store)
    delta=next(x for x in campaigns if x["asset_symbol"]=="DELTA")

    assert delta["position_count"] == 2
    assert delta["open_positions"] == 1
    assert delta["closed_positions"] == 1
    assert delta["realised_position_pnl_usd"] == 10.0
    assert delta["open_position_pnl_usd"] == -3.0
    assert delta["known_campaign_pnl_usd"] == 7.0
    assert delta["lifetime_fees_usd"] == 8.0
    assert delta["transaction_costs_usd"] == 2.0

    # Wallet exposure is visible but deliberately excluded from known P/L because
    # no acquisition basis has been proven.
    assert delta["wallet_inventory"]["balance"] == 50.0
    assert delta["wallet_inventory"]["value_usd"] == 25.0
    assert delta["wallet_inventory"]["quality"] == "UNATTRIBUTED_COST_BASIS"
    assert delta["provenance"]["wallet_inventory_included_in_pnl"] is False
    assert delta["accounting_quality"] == "PARTIAL_WALLET_BASIS"

    # Child records remain their own truth; campaign aggregation did not rewrite P/L.
    closed_after=store.get_position(closed.id)
    open_after=store.get_position(open_pos.id)
    assert closed_after["reported_net_pnl"] == 10.0
    assert open_after["reported_net_pnl"] == 0.0
    assert closed_after["campaign_id"] == open_after["campaign_id"] == "campaign:ROBINHOOD_CHAIN:DELTA"


def test_campaign_timeline_uses_confirmed_events_and_inferred_evidence(tmp_path):
    store=Store(tmp_path/"timeline.sqlite3")
    p=_position("live:ROBINHOOD_CHAIN:9","WETH/CASHCAT","OPEN",1000,current_value=101.0)
    store.upsert_position(p)
    store.save_position_snapshot(p.id,_live_snapshot("CASHCAT","0xccc",100,0.5))
    store.record_financial_event(
        position_id=p.id,event_type="OPEN_POSITION",chain="ROBINHOOD_CHAIN",
        tx_hash="0xmint",gas_usd=0.02,status="CONFIRMED",payload={"forecast_id":"f1"},
    )

    campaign=next(x for x in build_campaigns(store) if x["asset_symbol"]=="CASHCAT")
    assert campaign["provenance"]["financial_events"] == 1
    assert any(x["type"]=="OPEN_POSITION" and x["tx_hash"]=="0xmint" for x in campaign["timeline"])
