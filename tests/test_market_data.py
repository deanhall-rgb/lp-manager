from lp_manager.market_data import GeckoTerminalClient


def test_normalise_pool_reads_dex_relationship_and_tokens():
    payload={
      "included":[
        {"type":"token","id":"base_0x1","attributes":{"address":"0x1","symbol":"WETH","name":"Wrapped Ether"}},
        {"type":"token","id":"base_0x2","attributes":{"address":"0x2","symbol":"USDC","name":"USD Coin"}},
      ]
    }
    row={"id":"base_pool","attributes":{"address":"0xpool","name":"WETH / USDC","reserve_in_usd":"1200000","volume_usd":{"h24":"500000"}},"relationships":{"base_token":{"data":{"id":"base_0x1"}},"quote_token":{"data":{"id":"base_0x2"}},"dex":{"data":{"id":"uniswap_v3"}}}}
    got=GeckoTerminalClient._normalise_pool(row,payload,"BASE")
    assert got["protocol"] == "UNISWAP_V3"
    assert got["pair"] == "WETH/USDC"
    assert got["tvl_usd"] == 1200000
