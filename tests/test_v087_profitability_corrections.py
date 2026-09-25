from __future__ import annotations

from copy import deepcopy

import pytest

from lp_manager.fee_metrics import forecast_fee_metrics, observed_fee_metrics
from lp_manager.portfolio_accounting import position_accounting
from lp_manager.price_units import display_lens, validate_display_lens
from lp_manager.profit_calibration import fee_calibration_for_pool
from lp_manager.profit_engine import (
    _boundary_inventory_outcomes,
    _diverse_alternatives,
    _load_pool_and_history,
    recommend_profit_range,
)
from lp_manager.strategy_lab import analyse_live_pool


class MemoryStore:
    def __init__(self):
        self.settings = {}
        self.positions = []
        self.snapshots = {}

    def get_setting(self, key, default=None):
        return deepcopy(self.settings.get(key, default))

    def set_setting(self, key, value):
        self.settings[key] = deepcopy(value)

    def list_positions(self, status=None):
        rows = deepcopy(self.positions)
        if status:
            rows = [x for x in rows if str(x.get("status")) == str(status)]
        return rows

    def get_position_snapshot(self, position_id):
        return deepcopy(self.snapshots.get(position_id, {}))


def test_observed_apr_is_suppressed_before_24h():
    m = observed_fee_metrics(
        {"age_days": 0.5, "fees_24h_usd": 4.0, "cumulative_earned_usd": 4.0},
        1000.0,
    )
    assert m["annualisation_suppressed"] is True
    assert m["spot_1d_annualised_fee_apr_pct"] is None
    assert m["since_open_annualised_fee_apr_pct"] is None
    assert m["confidence"] == "INSUFFICIENT"


def test_forecast_cash_identity_never_makes_net_exceed_fee_income_without_named_return():
    m = forecast_fee_metrics(
        capital_usd=1000.0,
        horizon_days=7.0,
        expected_fees_usd=23.65,
        expected_cash_costs_usd=2.75,
    )
    assert m["expected_net_usd"] == pytest.approx(20.90, abs=0.01)
    assert m["expected_net_usd"] <= m["expected_fees_usd"]
    assert m["identity"] == "NET = FORECAST_FEES + EXPLICIT_OTHER_RETURN - CASH_COSTS"


def test_weth_stable_execution_lens_rejects_one_dollar_as_stable_per_eth():
    bad = {
        "current": 0.998996,
        "lower": 0.91,
        "upper": 1.08,
        "unit": "USDG_PER_WETH",
        "token0_symbol": "WETH",
        "token1_symbol": "USDG",
    }
    checked = validate_display_lens(bad)
    assert checked["valid"] is False
    assert "IMPLAUSIBLE_STABLE_PER_ETH_PRICE" in checked["reasons"]

    good = display_lens("WETH", "USDG", 2449.92, 2924.86, 2625.0)
    assert good["unit"] == "USDG_PER_WETH"
    assert good["validation"]["valid"] is True


def test_young_exact_pool_calibration_is_shrunk_and_capped(monkeypatch):
    store = MemoryStore()
    pool = {
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDG"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 1_000_000,
        "fee_tier_bps": 5.0,
    }
    store.positions = [{
        "id": "p4",
        "status": "OPEN",
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "strategy_sleeve": "CORE_INCOME",
        "capital_value": 1000,
        "current_value": 1000,
        "lower_price": 2400,
        "upper_price": 3000,
        "current_price": 2650,
    }]
    store.snapshots["p4"] = {"market": pool, "pool_address": "0xpool"}
    store.settings["fees:tracker:p4"] = {
        "age_days": 1.05,
        "fees_24h_usd": 1000.0,
        "cumulative_earned_usd": 1050.0,
    }
    cal = fee_calibration_for_pool(store, pool, sleeve="CORE_INCOME")
    assert cal["exact_pool_samples"] == 1
    assert cal["factor"] <= 1.25
    assert cal["factor"] >= 0.80
    assert cal["confidence"] == "MODERATE"


def test_sub_24h_sample_is_excluded_from_calibration():
    store = MemoryStore()
    pool = {
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool",
        "pair": "WETH/USDG",
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDG"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 500_000,
        "fee_tier_bps": 5.0,
    }
    store.positions = [{
        "id": "p4", "status": "OPEN", "chain": "ROBINHOOD_CHAIN",
        "pool_address": "0xpool", "pair": "WETH/USDG",
        "strategy_sleeve": "CORE_INCOME", "capital_value": 1000,
        "current_value": 1000, "lower_price": 2400, "upper_price": 3000,
        "current_price": 2650,
    }]
    store.snapshots["p4"] = {"market": pool}
    store.settings["fees:tracker:p4"] = {
        "age_days": 0.5, "fees_24h_usd": 20.0, "cumulative_earned_usd": 20.0
    }
    cal = fee_calibration_for_pool(store, pool, sleeve="CORE_INCOME")
    assert cal["sample_count"] == 0
    assert cal["factor"] == 1.0
    assert cal["confidence"] == "UNAVAILABLE"


def test_position_accounting_separates_absolute_pnl_from_lp_vs_hodl():
    position = {
        "capital_value": 1000.0,
        "current_value": 980.0,
        "unclaimed_fees": 35.0,
        "realised_fees": 0.0,
        "gas_costs": 5.0,
        "cost_basis_quality": "ONCHAIN_MINT_RECONSTRUCTED",
        "opened_at": 1000.0,
    }
    snapshot = {
        "token0": {"price_usd": 2500.0},
        "token1": {"price_usd": 1.0},
        "entry_evidence": {
            "opened_at": 1000.0,
            "token0_amount": 0.2,
            "token1_amount": 500.0,
            "entry_value_usd": 1000.0,
            "token0_price_usd": 2500.0,
            "token1_price_usd": 1.0,
            "basis_complete": True,
        },
    }
    tracker = {"cumulative_earned_usd": 35.0}
    a = position_accounting(position, snapshot, tracker)
    assert a["lp_plus_fees_usd"] == pytest.approx(1010.0)
    assert a["absolute_pnl_incl_fees_usd"] == pytest.approx(10.0)
    assert a["hodl_value_usd"] == pytest.approx(1000.0)
    assert a["lp_vs_hodl_usd"] == pytest.approx(10.0)


def test_core_weth_stable_boundary_outcomes_are_asymmetric():
    pool = {
        "base_token": {"symbol": "WETH"},
        "quote_token": {"symbol": "USDC"},
    }
    regime = {"direction": "BULLISH", "score": 62}
    x = _boundary_inventory_outcomes(pool, regime, "CORE_INCOME", 2400, 3100, 2700)
    assert x["below"]["asset"] == "WETH"
    assert x["above"]["asset"] == "USDC"
    assert x["below"]["utility_score"] > x["above"]["utility_score"]
    assert x["distance_below_current_pct"] > 0
    assert x["distance_above_current_pct"] > 0


def test_alternatives_are_materially_diverse():
    def row(width, score, skew=0):
        return {
            "lower": 100 - width / 2,
            "upper": 100 + width / 2,
            "width_pct": width,
            "profit_score": score,
            "skew_pct": skew,
        }
    best = row(20, 95)
    rows = [best, row(10, 85), row(18, 92), row(30, 80), row(22, 88, 9), row(25, 82)]
    alts = _diverse_alternatives(rows, best, 5)
    styles = {x["alternative_style"] for x in alts}
    assert "TIGHTER_AGGRESSIVE" in styles
    assert "WIDER_DURABLE" in styles
    assert "DIRECTIONAL" in styles


class OrientationMarket:
    def __init__(self):
        self.tokens = []

    def pool(self, chain, address):
        # Provider orientation is deliberately USDG/WETH.
        return {
            "chain": chain,
            "protocol": "UNISWAP_V3",
            "pool_address": address,
            "pair": "USDG/WETH",
            "base_token": {"address": "0x" + "1" * 40, "symbol": "USDG"},
            "quote_token": {"address": "0x" + "2" * 40, "symbol": "WETH"},
            "base_token_price_usd": 1.0,
            "quote_token_price_usd": 2625.0,
            "tvl_usd": 10_000_000,
            "volume_24h_usd": 5_000_000,
            "fee_tier_bps": 5.0,
        }

    def ohlcv_days(self, chain, address, days, timeframe="hour", token="base"):
        self.tokens.append(token)
        n = 72 if timeframe == "hour" else 30
        base = 2625.0 if token == "quote" else 1.0
        return [
            {"timestamp": 1_800_000_000 + i * 3600, "open": base, "high": base * 1.01,
             "low": base * 0.99, "close": base + i * 0.1, "volume": 100_000}
            for i in range(n)
        ]


def _usdgweth_onchain(address="0xpool", fee_bps=5.0):
    return {
        "ok": True,
        "chain": "ROBINHOOD_CHAIN",
        "pool_address": address,
        "token0": {"address": "0x" + "2" * 40, "symbol": "WETH", "decimals": 18},
        "token1": {"address": "0x" + "1" * 40, "symbol": "USDG", "decimals": 18},
        "fee_tier": int(fee_bps * 100),
        "fee_tier_bps": fee_bps,
        "tick_spacing": 10,
        "current_tick": 0,
        "active_liquidity": "1000000",
        "price_lens": {
            "current": 2625.0,
            "lower": 2625.0,
            "upper": 2625.0,
            "unit": "USDG_PER_WETH",
            "unit_label": "USDG per WETH",
            "token0_symbol": "WETH",
            "token1_symbol": "USDG",
        },
    }


def test_usdg_weth_history_uses_weth_price_not_usdg_one_dollar(monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: _usdgweth_onchain(address))
    market = OrientationMarket()
    pool, onchain, candles, provider, warning = _load_pool_and_history(
        market, "ROBINHOOD_CHAIN", "0xpool", 30
    )
    assert market.tokens[0] == "quote"
    assert candles[-1]["close"] > 2000
    assert pool["pair"] == "WETH/USDG"
    assert onchain["price_lens"]["current"] == 2625.0


class SimpleMarket:
    def pool(self, chain, address):
        return {
            "chain": chain, "protocol": "UNISWAP_V3", "pool_address": address,
            "pair": "WETH/USDC",
            "base_token": {"address": "0x" + "2" * 40, "symbol": "WETH"},
            "quote_token": {"address": "0x" + "1" * 40, "symbol": "USDC"},
            "tvl_usd": 20_000_000, "volume_24h_usd": 10_000_000,
            "fee_tier_bps": 5.0, "base_token_price_usd": 2700.0,
            "quote_token_price_usd": 1.0, "pool_created_at": "2025-01-01T00:00:00Z",
        }

    def ohlcv_days(self, chain, address, days, timeframe="hour", token="base"):
        n=max(180, int(days)*(24 if timeframe=="hour" else 1))
        step=3600 if timeframe=="hour" else 86400
        return [
            {
                "timestamp": 1_780_000_000+i*step,
                "open": 2550+i*0.1,
                "high": 2580+i*0.1,
                "low": 2520+i*0.1,
                "close": 2555+i*0.1,
                "volume": 450_000+(i%12)*10_000,
            }
            for i in range(n)
        ]


def test_strategy_and_profit_lab_share_one_recommendation(monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: {"ok": False, "error": "test"})
    market=SimpleMarket()
    store=MemoryStore()
    profit=recommend_profit_range(
        market, store, "ETHEREUM", "0xpool",
        horizon_days=7, capital=1000, sleeve="CORE_INCOME",
        compare_fee_tiers=False,
    )
    strategy=analyse_live_pool(
        market, "ETHEREUM", "0xpool", sleeve="CORE_INCOME",
        days=7, capital=1000, pool_fallback=market.pool("ETHEREUM","0xpool"), store=store,
    )
    assert strategy["recommended_range"]["lower"] == pytest.approx(profit["recommended_range"]["lower"])
    assert strategy["recommended_range"]["upper"] == pytest.approx(profit["recommended_range"]["upper"])
    assert strategy["policy_replay"]["range_selection"]["source"] == "UNIFIED_PROFIT_ENGINE"

class MultiTierMarket:
    def __init__(self):
        self.token0="0x"+"1"*40
        self.token1="0x"+"2"*40
        self.pools={
            "0x001":self._row("0x001",1.0,20_000_000,80_000_000),
            "0x005":self._row("0x005",5.0,50_000_000,80_000_000),
            "0x030":self._row("0x030",30.0,1_000_000,80_000_000),
        }

    def _row(self,address,fee,volume,tvl):
        return {
            "chain":"ETHEREUM","protocol":"UNISWAP_V3","pool_address":address,
            "pair":"WETH/USDC",
            "base_token":{"address":self.token0,"symbol":"WETH"},
            "quote_token":{"address":self.token1,"symbol":"USDC"},
            "tvl_usd":tvl,"volume_24h_usd":volume,"fee_tier_bps":fee,
            "base_token_price_usd":2700.0,"quote_token_price_usd":1.0,
            "pool_created_at":"2025-01-01T00:00:00Z",
        }

    def pool(self,chain,address):
        return deepcopy(self.pools[address])

    def token_pools(self,chain,token_address,page=1):
        return [deepcopy(x) for x in self.pools.values()] if page==1 else []

    def ohlcv_days(self,chain,address,days,timeframe="hour",token="base"):
        n=max(240,int(days)*(24 if timeframe=="hour" else 1))
        step=3600 if timeframe=="hour" else 86400
        vol=float(self.pools[address]["volume_24h_usd"])/(24 if timeframe=="hour" else 1)
        return [{
            "timestamp":1_800_000_000+i*step,
            "open":2670+i*0.03,"high":2705+i*0.03,"low":2640+i*0.03,
            "close":2680+i*0.03,"volume":vol,
        } for i in range(n)]


def test_acceptance_weth_usdc_selects_best_fee_tier_and_profit_guardrailed_range(monkeypatch):
    market=MultiTierMarket()
    def onchain(chain,address):
        row=market.pools[address]
        fee=float(row["fee_tier_bps"])
        return {
            "ok":True,"chain":"ETHEREUM","pool_address":address,
            "token0":{"address":market.token0,"symbol":"WETH","decimals":18},
            "token1":{"address":market.token1,"symbol":"USDC","decimals":6},
            "fee_tier":int(fee*100),"fee_tier_bps":fee,"tick_spacing":10,
            "price_lens":{
                "current":2700.0,"lower":2700.0,"upper":2700.0,
                "unit":"USDC_PER_WETH","unit_label":"USDC per WETH",
                "token0_symbol":"WETH","token1_symbol":"USDC",
            },
        }
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata",onchain)

    out=recommend_profit_range(
        market,MemoryStore(),"ETHEREUM","0x005",
        horizon_days=7,capital=1000,sleeve="CORE_INCOME",
        monthly_target_pct=10,compare_fee_tiers=True,
    )
    tiers={round(float(x["fee_tier_bps"]),2) for x in out["pool_comparison"]}
    assert {1.0,5.0,30.0}.issubset(tiers)
    assert out["pool_selection"]["selected_pool_address"]=="0x005"
    best=out["recommended_range"]
    assert best["selection_guardrail"]["eligible"] is True
    assert best["forecast"]["expected_net_usd"] <= best["forecast"]["expected_fees_usd"] + 1e-9
    assert best["forecast"]["forecast_fee_apr_pct"] > 0
    assert best["inventory_outcomes"]["below"]["asset"]=="WETH"
    assert best["inventory_outcomes"]["above"]["asset"]=="USDC"
    assert best["inventory_outcomes"]["distance_below_current_pct"] >= 2.5
    assert best["inventory_outcomes"]["distance_above_current_pct"] >= 2.5
    assert len(out["alternatives"]) >= 2


def test_acceptance_weth_usdg_full_profit_result_never_uses_one_dollar_execution_price(monkeypatch):
    monkeypatch.setattr(
        "lp_manager.profit_engine.read_v3_pool_metadata",
        lambda chain,address:_usdgweth_onchain(address),
    )
    out=recommend_profit_range(
        OrientationMarket(),MemoryStore(),"ROBINHOOD_CHAIN","0xpool",
        horizon_days=7,capital=1000,sleeve="CORE_INCOME",
        compare_fee_tiers=False,
    )
    assert out["spot"] > 2000
    assert out["price_lens"]["unit"]=="USDG_PER_WETH"
    assert out["recommended_range"]["lower"] > 100
    assert out["recommended_range"]["forecast"]["expected_net_usd"] <= out["recommended_range"]["forecast"]["expected_fees_usd"] + 1e-9


def test_acceptance_p4_p5_p6_have_authoritative_identity_opening_refs_and_accounting(tmp_path):
    from lp_manager.db import Store
    from lp_manager.live_positions import ScanResult, reconcile_scan
    from lp_manager.live_service import _AUTHORITATIVE_OPENINGS

    refs=_AUTHORITATIVE_OPENINGS["ROBINHOOD_CHAIN"]
    expected={1289953:"P4",1290067:"P5",1290077:"P6"}
    assert set(refs)==set(expected)

    db_path=tmp_path/"v087.sqlite3"
    store=Store(db_path)
    positions=[]
    for i,(token_id,label) in enumerate(expected.items(),start=1):
        opened=1_800_000_000.0+i*60
        entry=900.0+i*25
        snapshot={
            "read_at":opened+2*86400,
            "pool_address":"0x"+"3"*40,
            "range_unit":"USDG_PER_WETH",
            "opening_transaction_hash":refs[token_id],
            "opened_at":opened,
            "token0":{"symbol":"WETH","amount":0.20,"unclaimed":0.002,"price_usd":2700.0},
            "token1":{"symbol":"USDG","amount":450.0,"unclaimed":2.0,"price_usd":1.0},
            "entry_evidence":{
                "transaction_hash":refs[token_id],"opened_at":opened,
                "entry_value_usd":entry,"quality":"ONCHAIN_MINT_RECONSTRUCTED",
                "token0_amount":0.18,"token1_amount":430.0,
                "token0_price_usd":2750.0,"token1_price_usd":1.0,
                "basis_complete":True,
            },
        }
        positions.append({
            "pair":"WETH/USDG","lower_price":2400.0,"upper_price":3100.0,
            "current_price":2700.0,"current_value":990.0+i*10,
            "unclaimed_fees":7.4+i,"token_id":str(token_id),
            "active_liquidity":True,"opened_at":opened,"snapshot":snapshot,
        })

    reconcile_scan(store,ScanResult("ROBINHOOD_CHAIN",True,positions,latest_block=999))
    for token_id,label in expected.items():
        row=store.find_position_by_token("ROBINHOOD_CHAIN",str(token_id))
        assert row is not None
        assert str(row["display_name"]).startswith(label)
        assert row["opened_at"] > 0
        assert row["capital_value"] > 0
        snap=store.get_position_snapshot(row["id"])
        assert snap["opening_transaction_hash"]==refs[token_id]
        acct=position_accounting(row,snap,store.get_setting(f"fees:tracker:{row['id']}",{}) or {})
        assert acct["cost_basis_usd"] > 0
        assert acct["fees_earned_usd"] >= 0
        assert acct["absolute_pnl_incl_fees_usd"] == pytest.approx(acct["lp_plus_fees_usd"]-acct["cost_basis_usd"],abs=0.01)


def test_next_capital_allocator_exposes_expected_downside_efficiency_intervention_and_concentration():
    from lp_manager.capital_allocation import rank_capital_candidates
    rows=[
        {
            "chain":"ETHEREUM","pair":"WETH/USDC","expected_net_usd":28.0,
            "expected_net_pct":2.8,"expected_fees_usd":32.0,"low_net_usd":14.0,
            "confidence":"HIGH","recommendation":{"recommended_range":{
                "analysis":{"excursions":1},"forecast":{"expected_intervention_cost_usd":1.0}
            }},
        },
        {
            "chain":"BASE","pair":"WETH/USDC","expected_net_usd":25.0,
            "expected_net_pct":2.5,"expected_fees_usd":29.0,"low_net_usd":18.0,
            "confidence":"MODERATE","recommendation":{"recommended_range":{
                "analysis":{"excursions":0},"forecast":{"expected_intervention_cost_usd":0.5}
            }},
        },
    ]
    existing=[{"chain":"ETHEREUM","pair":"WETH/USDC","current_value":5000.0}]
    ranked=rank_capital_candidates(rows,capital_usd=1000.0,open_positions=existing)
    assert ranked
    for row in ranked:
        evidence=row["allocation_evidence"]
        assert "expected_net" in evidence
        assert "downside_case" in evidence
        assert "fee_efficiency" in evidence
        assert "intervention_quality" in evidence
        assert "existing_pair_concentration_pct" in evidence
        assert "existing_chain_concentration_pct" in evidence

def test_partial_opening_value_is_never_presented_as_absolute_pnl():
    position={
        "source":"live_chain",
        "capital_value":100.0,
        "current_value":200.0,
        "unclaimed_fees":10.0,
        "cost_basis_quality":"ONCHAIN_MINT_RECONSTRUCTED",
        "opened_at":1_800_000_000.0,
    }
    snapshot={
        "token0":{"price_usd":2700.0},
        "token1":{"price_usd":1.0},
        "entry_evidence":{
            "transaction_hash":"0xopen",
            "opened_at":1_800_000_000.0,
            "token0_amount":0.05,
            "token1_amount":100.0,
            "token0_price_usd":0.0,
            "token1_price_usd":1.0,
            "entry_value_usd":100.0,
            "partial_entry_value_usd":100.0,
            "basis_complete":False,
            "quality":"ONCHAIN_AMOUNTS_PARTIAL_PRICING",
        },
    }
    a=position_accounting(position,snapshot,{})
    assert a["basis_ready"] is False
    assert a["absolute_pnl_incl_fees_usd"] is None
    assert a["absolute_return_incl_fees_pct"] is None
    # Opening inventory is still useful for HODL even though opening USD basis is not.
    assert a["hodl_value_usd"] == pytest.approx(235.0)


def test_authoritative_p4_p5_p6_labels_replace_stale_p6_p7_p8_on_reconcile(tmp_path):
    from lp_manager.db import Store
    from lp_manager.live_positions import ScanResult, reconcile_scan
    from lp_manager.models import Position

    store=Store(tmp_path/"labels.sqlite3")
    stale={
        "1289953":"P6 · WETH/USDG",
        "1290067":"P7 · WETH/DELTA",
        "1290077":"P8 · WETH/HOOKR",
    }
    pairs={"1289953":"WETH/USDG","1290067":"WETH/DELTA","1290077":"WETH/HOOKR"}
    for tid,name in stale.items():
        store.upsert_position(Position(
            id=f"live:ROBINHOOD_CHAIN:{tid}",protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",
            pair=pairs[tid],status="OPEN",lower_price=1,upper_price=2,current_price=1.5,
            capital_value=100,current_value=100,unclaimed_fees=0,fees_today=0,fees_7d=0,
            fees_30d=0,realised_fees=0,estimated_il=0,gas_costs=0,apr_current=0,apr_7d=0,
            opened_at=1234,token_id=tid,source="live_chain",display_name=name,
            cost_basis_quality="FIRST_OBSERVED",
        ))
    rows=[]
    for tid,pair in pairs.items():
        rows.append({
            "pair":pair,"lower_price":1,"upper_price":2,"current_price":1.5,
            "current_value":100,"unclaimed_fees":0,"token_id":tid,
            "active_liquidity":True,"opened_at":1234,
            "snapshot":{"live":True,"opened_at":1234,"entry_evidence":{}},
        })
    reconcile_scan(store,ScanResult("ROBINHOOD_CHAIN",True,rows,latest_block=10))
    assert store.find_position_by_token("ROBINHOOD_CHAIN","1289953")["display_name"].startswith("P4")
    assert store.find_position_by_token("ROBINHOOD_CHAIN","1290067")["display_name"].startswith("P5")
    assert store.find_position_by_token("ROBINHOOD_CHAIN","1290077")["display_name"].startswith("P6")


def test_reconcile_demotes_old_partial_onchain_basis_instead_of_showing_wild_pnl(tmp_path):
    from lp_manager.db import Store
    from lp_manager.live_positions import ScanResult, reconcile_scan
    from lp_manager.models import Position

    store=Store(tmp_path/"partial.sqlite3")
    pid="live:ROBINHOOD_CHAIN:1289953"
    store.upsert_position(Position(
        id=pid,protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",pair="WETH/USDG",status="OPEN",
        lower_price=2400,upper_price=3000,current_price=2700,
        capital_value=100,current_value=200,unclaimed_fees=5,fees_today=0,fees_7d=0,fees_30d=0,
        realised_fees=0,estimated_il=0,gas_costs=0,apr_current=0,apr_7d=0,opened_at=2000,
        token_id="1289953",source="live_chain",display_name="P6 · WETH/USDG",
        cost_basis_quality="ONCHAIN_MINT_RECONSTRUCTED",
    ))
    store.save_position_snapshot(pid,{
        "live":True,"opened_at":2000,
        "entry_evidence":{
            "transaction_hash":"0xold","opened_at":2000,
            "token0_amount":0.05,"token1_amount":100,
            "token0_price_usd":0,"token1_price_usd":1,
            "entry_value_usd":100,"basis_complete":False,
            "quality":"ONCHAIN_AMOUNTS_PARTIAL_PRICING",
        },
    })
    reconcile_scan(store,ScanResult("ROBINHOOD_CHAIN",True,[{
        "pair":"WETH/USDG","lower_price":2400,"upper_price":3000,"current_price":2700,
        "current_value":205,"unclaimed_fees":5,"token_id":"1289953","active_liquidity":True,
        "opened_at":2000,"snapshot":{
            "live":True,"opened_at":2000,
            "entry_evidence":{
                "transaction_hash":"0xopen","opened_at":2000,
                "token0_amount":0.05,"token1_amount":100,
                "token0_price_usd":0,"token1_price_usd":1,
                "entry_value_usd":0,"partial_entry_value_usd":100,
                "basis_complete":False,"quality":"ONCHAIN_AMOUNTS_PARTIAL_PRICING",
            },
        },
    }],latest_block=10))
    row=store.get_position(pid)
    assert row["cost_basis_quality"]=="FIRST_OBSERVED"
    assert row["capital_value"]==pytest.approx(205.0)
    acct=position_accounting(row,store.get_position_snapshot(pid),{})
    assert acct["basis_ready"] is False
    assert acct["absolute_pnl_incl_fees_usd"] is None

