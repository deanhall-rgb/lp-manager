from pathlib import Path
from lp_manager.db import Store


def test_opportunity_status_is_persistent(tmp_path: Path):
    store=Store(tmp_path/'db.sqlite')
    saved=store.upsert_opportunity(
        candidate={"chain":"BASE","protocol":"UNISWAP_V3","pair":"WETH/USDC","pool_address":"0xabc"},
        evaluation={"preferred_sleeve":"CORE_INCOME","preferred_score":90},
        status="CANDIDATE",
    )
    row=store.set_opportunity_status(saved["id"], "APPROVED")
    assert row and row["status"] == "APPROVED"
