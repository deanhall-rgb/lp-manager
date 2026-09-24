from lp_manager.strategy_lab import analyse_live_pool


class FakeMarket:
    def resolve_pool(self, chain, address):
        return "ETHEREUM", {"chain":"ETHEREUM","pair":"WETH/USDC","pool_address":address,"protocol":"UNISWAP_V3","tvl_usd":50_000_000,"volume_24h_usd":20_000_000,"fee_tier_bps":5,"base_token_price_usd":2500,"base_token":{"symbol":"WETH"},"quote_token":{"symbol":"USDC"},"pool_created_at":"2024-01-01T00:00:00Z"}
    def ohlcv_days(self, chain, address, days):
        rows=[]
        price=2000.0
        for i in range(max(24*days,100)):
            price *= 1.00012
            rows.append({"timestamp":i*3600,"open":price*.999,"high":price*1.003,"low":price*.997,"close":price,"volume":200_000})
        return rows


def test_strategy_lab_includes_regime_economics_and_targets():
    r=analyse_live_pool(FakeMarket(),"ROBINHOOD_CHAIN","0xabc",sleeve="CORE_INCOME",days=30,capital=5000,target_monthly_pct=10)
    assert r["pool"]["chain"] == "ETHEREUM"
    assert r["regime"]["direction"] in {"BULLISH","NEUTRAL","BEARISH"}
    assert r["recommended_range"]["economics"]["estimated"] is True
    assert r["target_comparison"]["month"]["target"] > 0
    assert r["recommended_range"]["score"] > 0
