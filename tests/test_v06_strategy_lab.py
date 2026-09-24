from lp_manager.strategy_lab import analyse_live_pool
from lp_manager.replay import demo_replay_candles


class FakeMarket:
    def pool(self, chain, address):
        return {
            "chain": chain,
            "protocol": "UNISWAP_V3",
            "pool_address": address,
            "pair": "WETH/USDC",
            "base_token": {"symbol":"WETH"},
            "quote_token": {"symbol":"USDC"},
            "tvl_usd": 25_000_000,
            "volume_24h_usd": 8_000_000,
            "pool_created_at": "2025-01-01T00:00:00Z",
        }

    def ohlcv_days(self, chain, address, days):
        return demo_replay_candles("CORE_TREND", points=max(240, days * 24))


def test_strategy_lab_returns_ranked_ranges_and_replay():
    out = analyse_live_pool(FakeMarket(), "BASE", "0xpool", sleeve="CORE_INCOME", days=30, capital=5000)
    assert out["recommended_range"]["rank"] == 1
    assert len(out["recommendations"]) == 5
    assert out["policy_replay"]["no_lookahead"] is True
    assert out["recommended_range"]["active_time_pct"] >= 0
    assert out["price_series"]
