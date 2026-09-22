from lp_manager.data_contracts import validate_pool_snapshot, validate_position_snapshot


def test_stale_pool_snapshot_is_rejected():
    row=validate_pool_snapshot({"chain":"BASE","protocol":"UNISWAP_V3","pair":"WETH/USDC","pool_address":"0xabc","observed_at":100,"price":4000,"tvl_usd":1_000_000,"volume_24h_usd":500_000,"source":"test"},now=1000,max_age_seconds=300)
    assert row["valid"] is False
    assert "STALE_SNAPSHOT" in row["errors"]


def test_valid_position_contract():
    row=validate_position_snapshot({"chain":"BASE","protocol":"UNISWAP_V3","position_id":"1","observed_at":1000,"current_price":4000,"lower_price":3000,"upper_price":5000,"liquidity":123,"source":"rpc"},now=1010)
    assert row["valid"] is True
