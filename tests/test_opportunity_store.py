from pathlib import Path
from lp_manager.db import Store


def test_opportunity_roundtrip(tmp_path: Path):
    store=Store(tmp_path/'db.sqlite')
    candidate={"chain":"BASE","protocol":"UNISWAP_V3","pair":"WETH/USDC","pool_address":"0xabc"}
    evaluation={"preferred_sleeve":"CORE_INCOME","preferred_score":91.2}
    store.upsert_opportunity(candidate=candidate,evaluation=evaluation,status="CANDIDATE")
    rows=store.list_opportunities()
    assert rows[0]["pair"] == "WETH/USDC"
    assert rows[0]["candidate"]["pool_address"] == "0xabc"
