from __future__ import annotations

import os

from lp_manager.chain_registry import CHAINS
from lp_manager.market_data import GeckoTerminalClient, MarketDataError
import lp_manager.strategy_lab as strategy_lab


def test_robinhood_blockscout_uses_current_mainnet_host():
    assert CHAINS["ROBINHOOD_CHAIN"].explorer_api_base == "https://robinhoodchain.blockscout.com/api/v2"


class _429Session:
    def __init__(self):
        self.headers={}
        self.calls=[]
    def get(self, url, params=None, timeout=None):
        self.calls.append((url,params))
        class R:
            status_code=429
            headers={"Retry-After":"1"}
            def raise_for_status(self):
                raise RuntimeError("429")
            def json(self): return {}
        return R()


def test_resolve_pool_does_not_probe_other_chains_after_rate_limit(monkeypatch):
    client=GeckoTerminalClient()
    calls=[]
    def bad_pool(chain,address):
        calls.append(chain)
        raise MarketDataError("GeckoTerminal request failed after retries: rate limited (429)")
    monkeypatch.setattr(client,"pool",bad_pool)
    try:
        client.resolve_pool("ETHEREUM","0xpool")
    except MarketDataError:
        pass
    assert calls == ["ETHEREUM"]


class _FallbackMarket:
    def pool(self, chain, address):
        raise MarketDataError("GeckoTerminal rate limited (429)")
    def ohlcv_days(self, chain, address, days, timeframe="hour"):
        raise MarketDataError("GeckoTerminal rate limited (429)")
    def alchemy_pool_history(self, chain, onchain, days, timeframe="hour"):
        n=max(30, days*(24 if timeframe=="hour" else 1))
        step=3600 if timeframe=="hour" else 86400
        return [{"timestamp":i*step,"open":2500+i*.5,"high":2500+i*.5,"low":2500+i*.5,"close":2500+i*.5,"volume":0,"source":"ALCHEMY_TOKEN_PRICE_POINTS"} for i in range(n)]


def test_strategy_lab_uses_alchemy_history_when_gecko_is_rate_limited(monkeypatch):
    monkeypatch.setattr(strategy_lab,"read_v3_pool_metadata",lambda chain,address:{
        "ok":True,"chain":"ETHEREUM","pool_address":address,"fee_tier":500,"fee_tier_bps":5.0,"tick_spacing":10,
        "token0":{"address":"0x"+"1"*40,"symbol":"USDC","decimals":6},
        "token1":{"address":"0x"+"2"*40,"symbol":"WETH","decimals":18},
        "price_lens":{"current":2600.0,"unit":"USDC_PER_WETH","unit_label":"USDC per WETH"},
    })
    fallback={"chain":"ETHEREUM","protocol":"UNISWAP_V3","pool_address":"0xpool","pair":"WETH/USDC","base_token":{"address":"0x"+"2"*40,"symbol":"WETH"},"quote_token":{"address":"0x"+"1"*40,"symbol":"USDC"},"tvl_usd":50_000_000,"volume_24h_usd":20_000_000,"pool_created_at":"2024-01-01T00:00:00Z"}
    out=strategy_lab.analyse_live_pool(_FallbackMarket(),"ETHEREUM","0xpool",sleeve="CORE_INCOME",days=30,capital=1000,pool_fallback=fallback)
    assert out["history_source"]["provider"] == "ALCHEMY_TOKEN_PRICE_FALLBACK"
    assert out["history_samples"] >= 24
    assert out["recommended_range"]["lower"] > 0
