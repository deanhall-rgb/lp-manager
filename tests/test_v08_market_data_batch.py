from lp_manager.market_data import GeckoTerminalClient


def test_token_prices_uses_batched_simple_endpoint(monkeypatch):
    client=GeckoTerminalClient()
    seen=[]
    def fake_get(path, params=None):
        seen.append(path)
        return {"data":{"attributes":{"token_prices":{"0xaaa":"1.25","0xbbb":"2.5"}}}}
    monkeypatch.setattr(client,"_get",fake_get)
    result=client.token_prices("ROBINHOOD_CHAIN",["0xAAA","0xBBB"])
    assert result=={"0xaaa":1.25,"0xbbb":2.5}
    assert len(seen)==1
    assert "/simple/networks/robinhood/token_price/" in seen[0]
