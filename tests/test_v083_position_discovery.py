import pytest
pytest.importorskip("web3")

from pathlib import Path

from lp_manager.chain_registry import CHAINS
from lp_manager.db import Store
from lp_manager.live_positions import ScanResult, _blockscout_nft_items, _extract_transfer_ids_from_receipt, reconcile_scan
from lp_manager.models import Position


def _topic_addr(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().replace("0x", "")


def _topic_uint(value: int) -> str:
    return "0x" + int(value).to_bytes(32, "big").hex()


def _pos(pid: str, token_id: str, *, display_name: str = "Existing LP") -> Position:
    return Position(
        id=pid, protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair="WETH/DELTA", status="OPEN",
        lower_price=80_000, upper_price=140_000, current_price=100_000,
        capital_value=100, current_value=105, unclaimed_fees=5, fees_today=0, fees_7d=0, fees_30d=0,
        realised_fees=0, estimated_il=0, gas_costs=0, apr_current=0, apr_7d=0, opened_at=1,
        token_id=token_id, source="live_chain", display_name=display_name, pool_address="0x" + "1"*40,
        range_unit="DELTA_PER_WETH",
    )


def test_robinhood_has_owned_nft_discovery_endpoint():
    assert CHAINS["ROBINHOOD_CHAIN"].explorer_api_base == "https://robinhoodchain.blockscout.com/api/v2"


def test_blockscout_parser_filters_to_position_manager():
    manager=CHAINS["ROBINHOOD_CHAIN"].position_manager
    payload={"items":[
        {"id":"1300001","token":{"address_hash":manager}},
        {"id":"999","token":{"address_hash":"0x"+"2"*40}},
    ]}
    assert _blockscout_nft_items(payload,manager)=={1300001}


def test_receipt_parser_finds_v3_nft_transfer_to_wallet():
    manager=CHAINS["ROBINHOOD_CHAIN"].position_manager
    wallet="0x"+"a"*40
    from lp_manager.live_positions import TRANSFER_TOPIC
    receipt={"logs":[{"address":manager,"topics":[TRANSFER_TOPIC,_topic_addr("0x"+"0"*40),_topic_addr(wallet),_topic_uint(1300002)]}]}
    assert _extract_transfer_ids_from_receipt(receipt,manager,wallet)=={1300002}


def test_transient_row_error_does_not_false_close_known_position(tmp_path: Path):
    store=Store(tmp_path/"db.sqlite3")
    store.upsert_position(_pos("live:ROBINHOOD_CHAIN:1300003","1300003"))
    result=ScanResult("ROBINHOOD_CHAIN",True,[{"token_id":"1300003","error":"temporary RPC failure"}],latest_block=123)
    reconcile_scan(store,result)
    assert store.get_position("live:ROBINHOOD_CHAIN:1300003")["status"]=="OPEN"


def test_new_live_positions_receive_operator_sequence_after_historical_lp1_to_lp3(tmp_path: Path):
    store=Store(tmp_path/"db.sqlite3")
    for n in (1,2,3):
        p=_pos(f"hist{n}",str(1200000+n),display_name=f"DELTA LP{n}")
        p.status="CLOSED"; p.source="historical_research"
        store.upsert_position(p)
    snap={"pool_address":"0x"+"3"*40,"range_unit":"DELTA_PER_WETH"}
    result=ScanResult("ROBINHOOD_CHAIN",True,[{
        "pair":"WETH/DELTA","lower_price":80_000,"upper_price":140_000,"current_price":106_000,
        "current_value":200,"unclaimed_fees":3,"token_id":"1300004","active_liquidity":True,"opened_at":1234,"snapshot":snap,
    }],latest_block=999)
    reconcile_scan(store,result)
    row=store.find_position_by_token("ROBINHOOD_CHAIN","1300004")
    assert row["display_name"].startswith("P4")
    assert row["opened_at"]==1234
