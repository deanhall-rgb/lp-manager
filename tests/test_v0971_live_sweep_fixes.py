from pathlib import Path

import pytest

from lp_manager.execution_pricing import complete_pool_usd_marks


def _qnt_weth_meta():
    return {
        "token0": {"address": "0x" + "1" * 40, "symbol": "QNT"},
        "token1": {"address": "0x" + "2" * 40, "symbol": "WETH"},
        "price_lens": {
            "current": 10.0,
            "unit": "QNT_PER_WETH",
            "unit_label": "QNT per WETH",
        },
    }


def test_execution_auto_size_derives_qnt_mark_from_weth_and_pool_ratio():
    meta = _qnt_weth_meta()
    weth = meta["token1"]["address"].lower()
    qnt = meta["token0"]["address"].lower()

    out = complete_pool_usd_marks(meta, {weth: 2000.0})

    assert out["anchored"] is True
    assert out["marks"][weth] == pytest.approx(2000.0)
    assert out["marks"][qnt] == pytest.approx(200.0)
    assert out["price0_usd"] == pytest.approx(200.0)
    assert out["price1_usd"] == pytest.approx(2000.0)
    assert out["sources"]["QNT"] == "POOL_RATIO_FROM_WETH"


def test_execution_auto_size_can_anchor_from_stablecoin_without_second_provider_mark():
    meta = {
        "token0": {"address": "0x" + "3" * 40, "symbol": "WPOL"},
        "token1": {"address": "0x" + "4" * 40, "symbol": "USDT0"},
        "price_lens": {
            "current": 0.11,
            "unit": "USDT0_PER_WPOL",
        },
    }

    out = complete_pool_usd_marks(meta, {})

    assert out["anchored"] is True
    assert out["price1_usd"] == pytest.approx(1.0)
    assert out["price0_usd"] == pytest.approx(0.11)
    assert out["sources"]["USDT0"] == "USD_STABLE_PARITY"
    assert out["sources"]["WPOL"] == "POOL_RATIO_FROM_USDT0"


def test_execution_auto_size_refuses_unanchored_volatile_pair():
    meta = {
        "token0": {"address": "0x" + "5" * 40, "symbol": "AAA"},
        "token1": {"address": "0x" + "6" * 40, "symbol": "BBB"},
        "price_lens": {"current": 2.0, "unit": "BBB_PER_AAA"},
    }

    out = complete_pool_usd_marks(meta, {})

    assert out["anchored"] is False
    assert out["price0_usd"] == 0
    assert out["price1_usd"] == 0


def test_profit_lab_never_reuses_different_request_as_stale_fallback():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")
    # Exact cache key includes horizon/capital/sleeve/target. The old broad
    # same-pool fallback caused 7-day £1,000 results to appear under 1/3-day
    # £270 form inputs after a provider failure.
    assert 'cached=store.get_setting(cache_key,None)' in api
    assert 'last=store.get_setting("profit:last_recommendation:v093",None)' not in api


def test_profit_form_hides_previous_result_when_inputs_change():
    root = Path(__file__).parents[1]
    js = (root / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")

    assert "function markProfitInputsDirty()" in js
    assert "The previous result has been hidden" in js
    assert "'#profit-days'" in js
    assert "'#profit-capital'" in js


def _series(symbol: str, price: float, count: int = 72):
    import time
    now=int(time.time())
    start=now-(count-1)*3600
    return [
        {"timestamp":start+i*3600,"price_usd":price*(1+i*0.0005),"symbol":symbol}
        for i in range(count)
    ]


def _qnt_weth_history_meta(address: str):
    return {
        "ok":True,
        "chain":"ETHEREUM",
        "pool_address":address,
        "token0":{"address":"0x"+"1"*40,"symbol":"QNT","decimals":18},
        "token1":{"address":"0x"+"2"*40,"symbol":"WETH","decimals":18},
        "fee_tier":3000,
        "fee_tier_bps":30.0,
        "price_lens":{
            "current":10.0,
            "unit":"QNT_PER_WETH",
            "unit_label":"QNT per WETH",
            "token0_symbol":"QNT",
            "token1_symbol":"WETH",
        },
    }


def test_nonstable_profit_history_can_use_global_symbol_pair_ratio(monkeypatch):
    import lp_manager.profit_engine as profit_engine

    address="0x"+"a"*40
    meta=_qnt_weth_history_meta(address)

    class Market:
        def alchemy_pool_history(self,*args,**kwargs):
            return []
        def alchemy_symbol_history(self,symbol,days,*,timeframe="hour"):
            return _series(symbol,2000.0 if symbol=="WETH" else 200.0)
        def ohlcv_days(self,*args,**kwargs):
            raise AssertionError("Gecko should not be needed when symbol ratio succeeds")

    monkeypatch.setattr(profit_engine,"read_v3_pool_metadata",lambda *a,**k:meta)
    monkeypatch.setattr(profit_engine,"read_v3_observation_history",lambda *a,**k:[])

    pool={
        "chain":"ETHEREUM","pool_address":address,"pair":"QNT/WETH",
        "base_token":{"symbol":"QNT"},"quote_token":{"symbol":"WETH"},
        "tvl_usd":900_000,"volume_24h_usd":2_000_000,
    }
    _,_,candles,provider,_=profit_engine._load_pool_and_history(
        Market(),"ETHEREUM",address,30,pool_fallback=pool,store=None,
    )
    assert len(candles)>=24
    assert provider=="ALCHEMY_SYMBOL_PAIR_RATIO"
    assert candles[-1]["close"]==pytest.approx(10.0)


def test_nonstable_profit_history_can_reconstruct_dual_gecko_pool_ratio(monkeypatch):
    import time
    import lp_manager.profit_engine as profit_engine

    address="0x"+"b"*40
    meta=_qnt_weth_history_meta(address)
    now=int(time.time())
    start=now-71*3600

    class Market:
        def alchemy_pool_history(self,*args,**kwargs):
            return []
        def alchemy_symbol_history(self,*args,**kwargs):
            return []
        def ohlcv_days(self,chain,pool,days,*,timeframe="hour",token="base"):
            usd=200.0 if token=="base" else 2000.0
            return [
                {"timestamp":start+i*3600,"open":usd,"high":usd,"low":usd,"close":usd,"volume":1}
                for i in range(72)
            ]

    monkeypatch.setattr(profit_engine,"read_v3_pool_metadata",lambda *a,**k:meta)
    monkeypatch.setattr(profit_engine,"read_v3_observation_history",lambda *a,**k:[])

    pool={
        "chain":"ETHEREUM","pool_address":address,"pair":"QNT/WETH",
        "base_token":{"symbol":"QNT"},"quote_token":{"symbol":"WETH"},
        "tvl_usd":900_000,"volume_24h_usd":2_000_000,
    }
    _,_,candles,provider,_=profit_engine._load_pool_and_history(
        Market(),"ETHEREUM",address,30,pool_fallback=pool,store=None,
    )
    assert len(candles)>=24
    assert provider=="GECKOTERMINAL_POOL_PAIR_RATIO"
    assert candles[-1]["close"]==pytest.approx(10.0)
