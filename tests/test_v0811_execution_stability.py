from __future__ import annotations

from pathlib import Path

import pytest

from lp_manager.live_v3_builder import _native_wrap_shortfall, scale_quote_to_capital
from lp_manager.live_positions import _apply_last_good_position_prices
from lp_manager.market_data import GeckoTerminalClient


def test_partial_alchemy_prices_are_completed_from_gecko(monkeypatch):
    client=GeckoTerminalClient()
    monkeypatch.setattr(client,"_alchemy_token_prices",lambda chain,addresses:{"0xaaa":1.25})
    seen=[]
    def fake_get(path,params=None):
        seen.append(path)
        return {"data":{"attributes":{"token_prices":{"0xbbb":"2.5"}}}}
    monkeypatch.setattr(client,"_get",fake_get)

    got=client.token_prices("ROBINHOOD_CHAIN",["0xAAA","0xBBB"])

    assert got=={"0xaaa":1.25,"0xbbb":2.5}
    assert len(seen)==1
    assert "0xbbb" in seen[0]
    assert "0xaaa" not in seen[0]


def test_wrap_shortfall_drops_to_zero_after_wrapped_balance_arrives():
    assert _native_wrap_shortfall(0.05,0.0)==pytest.approx(0.05)
    assert _native_wrap_shortfall(0.05,0.049)==pytest.approx(0.001)
    assert _native_wrap_shortfall(0.05,0.05)==pytest.approx(0.0)
    assert _native_wrap_shortfall(0.05,0.08)==pytest.approx(0.0)


def test_capital_sizing_preserves_range_token_ratio_and_budget():
    amount0,amount1=scale_quote_to_capital(
        amount0=1.0,
        amount1=0.004,
        price0_usd=0.20,
        price1_usd=2700.0,
        capital_usd=350.0,
    )
    assert amount1/amount0==pytest.approx(0.004)
    assert amount0*0.20+amount1*2700.0==pytest.approx(350.0)


def test_lp_last_good_price_fallback_only_fills_missing_same_token_marks():
    previous={
        "token0":{"address":"0xaaa","symbol":"HOOKR","price_usd":0.01},
        "token1":{"address":"0xbbb","symbol":"WETH","price_usd":2700.0},
    }
    current={
        "token0":{"address":"0xaaa","symbol":"HOOKR","price_usd":0.0,"amount":1000},
        "token1":{"address":"0xbbb","symbol":"WETH","price_usd":2710.0,"amount":0.01},
    }

    out,stale=_apply_last_good_position_prices(current,previous)

    assert out["token0"]["price_usd"]==pytest.approx(0.01)
    assert out["token0"]["price_stale"] is True
    assert out["token1"]["price_usd"]==pytest.approx(2710.0)
    assert stale==["HOOKR"]

    # Never copy a price across a changed token identity.
    changed={"token0":{"address":"0xccc","symbol":"OTHER","price_usd":0.0}}
    out2,stale2=_apply_last_good_position_prices(changed,previous)
    assert float(out2["token0"].get("price_usd") or 0)==0.0
    assert stale2==[]


def test_execution_desk_is_throttled_and_profit_capital_is_carried_forward():
    root=Path(__file__).parents[1]
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")
    api=(root/"lp_manager"/"api.py").read_text(encoding="utf-8")
    wallet=(root/"lp_manager"/"wallet.py").read_text(encoding="utf-8")
    live=(root/"lp_manager"/"live_positions.py").read_text(encoding="utf-8")

    assert "execTargetCapitalUsd" in js
    assert "/api/execution/open/capital-quote" in js
    assert "r.capital_usd||null" in js
    assert "setInterval(()=>{if(document.visibilityState==='visible'" in js
    assert "},30000)" in js
    assert "},5000)" not in js
    assert "tickMoved&&Date.now()-execLastRequoteAt>=30000" in js
    assert "Recheck & build final position" in js

    assert '@app.post("/api/execution/open/capital-quote")' in api
    assert "PROFIT_LAB_CAPITAL_AUTO_SIZE" in api

    assert "LAST_GOOD_PRICE_FALLBACK" in wallet
    assert "LIVE_CHAIN_LAST_GOOD_PRICE_FALLBACK" in live
