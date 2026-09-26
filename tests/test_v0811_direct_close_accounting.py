from __future__ import annotations

from web3 import Web3

from lp_manager.close_accounting import decode_collect_tokens, finalise_execution_close
from lp_manager.db import Store
from lp_manager.models import Position


def _position() -> Position:
    return Position(
        id="live:ROBINHOOD_CHAIN:1314761",
        protocol="UNISWAP_V3",
        chain="ROBINHOOD_CHAIN",
        pair="WETH/CASHCAT",
        status="OPEN",
        lower_price=13000,
        upper_price=16000,
        current_price=14500,
        capital_value=15.0,
        current_value=15.0,
        unclaimed_fees=0.0,
        fees_today=0.0,
        fees_7d=0.0,
        fees_30d=0.0,
        realised_fees=0.0,
        estimated_il=0.0,
        gas_costs=0.0,
        apr_current=0.0,
        apr_7d=0.0,
        opened_at=1_700_000_000,
        token_id="1314761",
        source="live_chain",
        display_name="P9 · WETH/CASHCAT",
        lifecycle_stage="ACTIVE",
        cost_basis_quality="ONCHAIN_MINT_RECONSTRUCTED",
        strategy_sleeve="TACTICAL_CAMPAIGN",
    )


def _snapshot() -> dict:
    return {
        "token_id":"1314761",
        "position_manager":"0x"+"11"*20,
        "token0":{
            "address":"0x"+"aa"*20,
            "symbol":"CASHCAT",
            "decimals":18,
            "amount":50.0,
            "unclaimed":0.0,
            "price_usd":0.10,
        },
        "token1":{
            "address":"0x"+"bb"*20,
            "symbol":"WETH",
            "decimals":18,
            "amount":0.004,
            "unclaimed":0.0,
            "price_usd":2500.0,
        },
        "current_value_usd":15.0,
        "unclaimed_fees_usd":0.0,
        "entry_evidence":{
            "transaction_hash":"0xopen",
            "opened_at":1_700_000_000,
            "token0_amount":50.0,
            "token1_amount":0.004,
            "token0_price_usd":0.10,
            "token1_price_usd":2500.0,
            "entry_value_usd":15.0,
            "basis_complete":True,
            "quality":"ONCHAIN_MINT_RECONSTRUCTED",
        },
    }


def _collect_receipt(snapshot: dict, token_id: int, amount0: float, amount1: float) -> dict:
    topic0="0x"+Web3.keccak(text="Collect(uint256,address,uint256,uint256)").hex().removeprefix("0x")
    topic1="0x"+int(token_id).to_bytes(32,"big").hex()
    recipient=(0).to_bytes(12,"big")+bytes.fromhex("22"*20)
    a0=int(amount0*(10**int(snapshot["token0"]["decimals"]))).to_bytes(32,"big")
    a1=int(amount1*(10**int(snapshot["token1"]["decimals"]))).to_bytes(32,"big")
    return {
        "logs":[{
            "address":snapshot["position_manager"],
            "topics":[topic0,topic1],
            "data":"0x"+(recipient+a0+a1).hex(),
        }]
    }


def test_decode_collect_tokens_reads_exact_close_distribution():
    snap=_snapshot()
    receipt=_collect_receipt(snap,1314761,50.0,0.004)
    got=decode_collect_tokens(receipt,snap,1314761)
    assert got["CASHCAT"] == 50.0
    assert got["WETH"] == 0.004


def test_confirmed_execution_close_becomes_closed_final_without_history_scan(tmp_path):
    store=Store(tmp_path/"direct_close.sqlite3")
    position=_position()
    snap=_snapshot()
    store.upsert_position(position)
    store.save_position_snapshot(position.id,snap)

    # Mint and close gas are already attributable to the position.
    store.record_financial_event(
        position_id=position.id,event_type="OPEN_POSITION",chain="ROBINHOOD_CHAIN",
        tx_hash="0xopen",gas_usd=0.03,status="CONFIRMED",
        payload={"forecast_id":"forecast-1"},
    )
    store.record_financial_event(
        position_id=position.id,event_type="CLOSE_POSITION",chain="ROBINHOOD_CHAIN",
        tx_hash="0xclose",amount_usd=0.0,gas_usd=0.02,status="CONFIRMED",
        payload={"collected_tokens":{"CASHCAT":50.0,"WETH":0.004}},
    )

    result=finalise_execution_close(
        store,
        position_id=position.id,
        chain="ROBINHOOD_CHAIN",
        tx_hash="0xclose",
        receipt=None,
        market=None,
    )

    assert result["ok"] is True
    final=result["closed_final"]
    assert final["complete"] is True
    assert final["history_scan_required"] is False
    assert final["accounting_source"] == "DIRECT_CONFIRMED_CLOSE_RECEIPT"
    assert final["close_proceeds_usd"] == 15.0
    assert final["gas_usd"] == 0.05
    assert final["realised_pnl_usd"] == -0.05

    row=store.get_position(position.id)
    assert row["status"] == "CLOSED"
    assert row["lifecycle_stage"] == "CLOSED_FINAL"
    assert row["pnl_quality"] == "ONCHAIN_EXECUTION_RECEIPT_FINAL"
    assert row["reported_net_pnl"] == -0.05

    saved=store.get_position_snapshot(position.id)
    assert saved["closed_history_mode"] == "DIRECT_EXECUTION_RECEIPT"
    assert saved["close_detection"]["history_mode"] == "NOT_REQUIRED"


def test_prerequisite_receipts_link_to_minted_position_by_forecast(tmp_path):
    store=Store(tmp_path/"execution_gas.sqlite3")
    position=_position()
    store.upsert_position(position)

    for event_type,tx_hash,gas in [
        ("WRAP_NATIVE","0xwrap",0.01),
        ("TOKEN_APPROVAL","0xapprove",0.02),
        ("OPEN_POSITION","0xmint",0.03),
    ]:
        store.record_financial_event(
            position_id=None,event_type=event_type,chain="ROBINHOOD_CHAIN",
            tx_hash=tx_hash,gas_usd=gas,status="CONFIRMED",
            payload={"forecast_id":"forecast-1"},
        )

    # The forecast row is optional for gas linkage; the forecast id in financial
    # events is the lifecycle correlation key.
    linked=store.reconcile_execution_opening("0xmint",position.id)

    assert linked["linked_events"] == 1
    assert linked["linked_prerequisites"] == 2
    events=store.list_financial_events(20,position.id)
    assert {e["event_type"] for e in events} == {
        "WRAP_NATIVE","TOKEN_APPROVAL","OPEN_POSITION"
    }
    row=store.get_position(position.id)
    assert row["gas_costs"] == 0.06
