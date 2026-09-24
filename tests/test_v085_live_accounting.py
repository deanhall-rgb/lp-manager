from __future__ import annotations

from types import SimpleNamespace

import pytest
from lp_manager.asset_lens import pool_price_lens
from lp_manager.strategy_lab import analyse_live_pool


def test_stable_major_pair_defaults_core():
    pytest.importorskip("web3")
    from lp_manager.live_positions import _auto_sleeve
    assert _auto_sleeve("WETH/USDG") == "CORE_INCOME"
    assert _auto_sleeve("WETH/USDC") == "CORE_INCOME"
    assert _auto_sleeve("WETH/DELTA") == "TACTICAL_CAMPAIGN"


def test_blockscout_opening_evidence_prefers_earliest_incoming(monkeypatch):
    pytest.importorskip("web3")
    from lp_manager.live_positions import _discover_opening_evidence_blockscout
    class Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"items":[
                {"block_number":20,"timestamp":"2026-09-24T07:00:00Z","transaction_hash":"0xlater","to":{"hash":"0xabc"},"from":{"hash":"0xdef"}},
                {"block_number":10,"timestamp":"2026-09-23T22:33:39Z","transaction_hash":"0xopen","to":{"hash":"0xabc"},"from":{"hash":"0x0000000000000000000000000000000000000000"}},
            ]}
    monkeypatch.setattr("lp_manager.live_positions.requests.get", lambda *a, **k: Resp())
    cfg=SimpleNamespace(explorer_api_base="https://example/api/v2",position_manager="0xmanager")
    row=_discover_opening_evidence_blockscout(cfg,"0xabc",123)
    assert row["transaction_hash"] == "0xopen"
    assert row["block_number"] == 10
    assert row["opened_at"] > 0


def test_erc20_opening_deposit_parser_uses_transfers_into_pool():
    pytest.importorskip("web3")
    from lp_manager.live_positions import _erc20_deposits_from_receipt
    transfer="0x" + "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    pool="0x"+"3"*40
    token0="0x"+"1"*40; token1="0x"+"2"*40
    topic=lambda a: "0x"+"0"*24+a[2:]
    receipt={"logs":[
        {"address":token0,"topics":[transfer,topic("0x"+"4"*40),topic(pool)],"data":hex(75_000_000_000_000_000)},
        {"address":token1,"topics":[transfer,topic("0x"+"5"*40),topic(pool)],"data":hex(6_136_607_000_000_000_000_000)},
        {"address":token1,"topics":[transfer,topic(pool),topic("0x"+"6"*40)],"data":hex(999)},
    ]}
    a0,a1=_erc20_deposits_from_receipt(receipt,pool=pool,token0=token0,token1=token1,dec0=18,dec1=18)
    assert round(a0,6) == 0.075
    assert round(a1,3) == 6136.607


class MemoryStore:
    def __init__(self): self.d={}
    def get_setting(self,k,d=None): return self.d.get(k,d)
    def set_setting(self,k,v): self.d[k]=v


def test_fee_tracker_counts_initial_unclaimed_and_future_token_increments():
    pytest.importorskip("web3")
    from lp_manager.live_positions import _rolling_fee_tracker
    st=MemoryStore(); opened=1_000_000.0
    snap={"read_at":opened+3600,"token0":{"unclaimed":0.01,"price_usd":2000},"token1":{"unclaimed":10,"price_usd":0.02}}
    first=_rolling_fee_tracker(st,"p1",snap,opened,300)
    assert round(first["cumulative_earned_usd"],2) == 20.20
    snap2={"read_at":opened+7200,"token0":{"unclaimed":0.011,"price_usd":2000},"token1":{"unclaimed":15,"price_usd":0.02}}
    second=_rolling_fee_tracker(st,"p1",snap2,opened,300)
    assert round(second["cumulative_earned_usd"],2) == 22.30
    assert second["fees_24h_usd"] >= 22.29
    assert second["annualised_fee_pace_pct"] > 0


def test_pool_price_lens_carries_explicit_execution_unit():
    pool={"base_token":{"symbol":"WETH"},"quote_token":{"symbol":"USDC"},"onchain":{"price_lens":{"unit":"USDC_PER_WETH","unit_label":"USDC per WETH"}}}
    lens=pool_price_lens(pool,lower=2200,upper=3300,current=2700)
    assert lens["unit"] == "USDC_PER_WETH"
    assert lens["unit_label"] == "USDC per WETH"


class FakeMarket:
    def pool(self, chain, address):
        return {"chain":chain,"protocol":"UNISWAP_V3","pool_address":address,"pair":"WETH/USDC","base_token":{"symbol":"WETH"},"quote_token":{"symbol":"USDC"},"tvl_usd":50_000_000,"volume_24h_usd":25_000_000,"fee_tier_bps":5,"base_token_price_usd":2700,"pool_created_at":"2024-01-01T00:00:00Z"}
    def ohlcv_days(self, chain, address, days, timeframe="hour"):
        n=max(100,days*(24 if timeframe=="hour" else 1)); step=3600 if timeframe=="hour" else 86400
        return [{"timestamp":i*step,"open":2200+i,"high":2210+i,"low":2190+i,"close":2200+i,"volume":1_000_000} for i in range(n)]


def test_strategy_lab_explains_target_gap(monkeypatch):
    monkeypatch.setattr("lp_manager.strategy_lab.read_v3_pool_metadata",lambda chain,address:{"ok":False,"error":"test"})
    out=analyse_live_pool(FakeMarket(),"ETHEREUM","0xpool",sleeve="CORE_INCOME",days=30,capital=1000,target_monthly_pct=10,pool_fallback=FakeMarket().pool("ETHEREUM","0xpool"))
    d=out["target_diagnostics"]
    assert d["target_month_usd"] == 100.0
    assert "estimated_operating_net_month_usd" in d
    assert "attainment_pct" in d
