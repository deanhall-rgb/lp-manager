from __future__ import annotations

import math
from pathlib import Path

import pytest

from lp_manager.config import Settings
from lp_manager.db import Store
from lp_manager.execution import ExecutionService
from lp_manager.financial_truth import portfolio_financial_truth
from lp_manager.live_scout import preliminary_pool_evaluation
from lp_manager.models import Position
from lp_manager.profit_engine import recommend_profit_range
from lp_manager.v3_math import Q96, amounts_raw_per_liquidity


def _position(
    pid: str,
    *,
    pair: str = "WETH/USDG",
    pool: str = "0xpool",
    capital: float = 100.0,
    current: float = 110.0,
    unclaimed: float = 5.0,
    realised: float = 2.0,
    gas: float = 1.0,
) -> Position:
    return Position(
        id=pid, protocol="UNISWAP_V3", chain="ROBINHOOD_CHAIN", pair=pair, status="OPEN",
        lower_price=2400.0, upper_price=3000.0, current_price=2700.0,
        capital_value=capital, current_value=current, unclaimed_fees=unclaimed,
        fees_today=0.0, fees_7d=0.0, fees_30d=0.0, realised_fees=realised,
        estimated_il=0.0, gas_costs=gas, apr_current=0.0, apr_7d=0.0,
        opened_at=1_800_000_000.0, token_id="123", source="live_chain",
        strategy_sleeve="CORE_INCOME", display_name="P4 · WETH/USDG",
        cost_basis_quality="ONCHAIN_MINT_RECONSTRUCTED", strategy_version="v0.8.8",
        pool_address=pool, range_unit="USDG_PER_WETH",
    )


def _verified_snapshot(pool: str = "0xpool") -> dict:
    return {
        "live": True, "chain": "ROBINHOOD_CHAIN", "pool_address": pool,
        "opened_at": 1_800_000_000.0,
        "token0": {"symbol": "WETH", "price_usd": 2700.0, "decimals": 18},
        "token1": {"symbol": "USDG", "price_usd": 1.0, "decimals": 18},
        "entry_evidence": {
            "transaction_hash": "0xopen", "opened_at": 1_800_000_000.0,
            "token0_amount": 0.02, "token1_amount": 46.0,
            "token0_price_usd": 2700.0, "token1_price_usd": 1.0,
            "entry_value_usd": 100.0, "basis_complete": True,
            "quality": "ONCHAIN_MINT_RECONSTRUCTED",
        },
    }


def test_major_stable_pair_is_core_inventory_even_when_pool_is_young():
    row = {
        "chain": "ROBINHOOD_CHAIN", "protocol": "UNISWAP_V3", "pair": "USDG/WETH",
        "base_token": {"symbol": "USDG"}, "quote_token": {"symbol": "WETH"},
        "tvl_usd": 250_000.0, "volume_24h_usd": 100_000.0,
        "pool_created_at": "2026-09-24T00:00:00Z",
        "price_change_24h_pct": 1.0,
    }
    out = preliminary_pool_evaluation(row)
    assert out["pair_policy_sleeve"] == "CORE_INCOME"
    assert out["preferred_sleeve"] == "CORE_INCOME"


class _RangeMarket:
    def pool(self, chain, address):
        return {
            "chain": chain, "protocol": "UNISWAP_V3", "pool_address": address,
            "pair": "WETH/USDC",
            "base_token": {"address": "0x" + "1" * 40, "symbol": "WETH"},
            "quote_token": {"address": "0x" + "2" * 40, "symbol": "USDC"},
            "tvl_usd": 50_000_000.0, "volume_24h_usd": 20_000_000.0,
            "fee_tier_bps": 5.0, "base_token_price_usd": 2700.0,
            "quote_token_price_usd": 1.0, "pool_created_at": "2024-01-01T00:00:00Z",
        }

    def ohlcv_days(self, chain, address, days, timeframe="hour", token="base"):
        n = max(24 * int(days), 240)
        rows = []
        for i in range(n):
            # A believable heavy-asset path: roughly +/- 2.5% cyclical movement.
            px = 2700.0 * (1.0 + 0.022 * math.sin(i / 20.0) + 0.004 * math.sin(i / 3.0))
            rows.append({
                "timestamp": 1_800_000_000 + i * 3600,
                "open": px * 0.999, "high": px * 1.003, "low": px * 0.997,
                "close": px, "volume": 800_000.0,
            })
        return rows


def test_seven_day_tactical_range_cannot_hide_a_30pct_eth_move(tmp_path, monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: {"ok": False, "error": "test"})
    store = Store(tmp_path / "range.sqlite3")
    out = recommend_profit_range(
        _RangeMarket(), store, "ETHEREUM", "0xpool",
        horizon_days=7, capital=1000.0, sleeve="TACTICAL_CAMPAIGN",
        compare_fee_tiers=False,
    )
    guard = out["recommended_range"]["selection_guardrail"]["edge_balance"]
    assert guard["farthest_edge_pct"] <= guard["max_farthest_edge_pct"] + 1e-6
    assert guard["max_farthest_edge_pct"] <= 14.0
    assert guard["asymmetry_ratio"] <= guard["max_asymmetry_ratio"] + 1e-6


def test_same_pair_owned_fee_prior_prevents_fake_zero_economics(tmp_path, monkeypatch):
    monkeypatch.setattr("lp_manager.profit_engine.read_v3_pool_metadata", lambda chain, address: {"ok": False, "error": "test"})
    store = Store(tmp_path / "prior.sqlite3")
    p = _position("p4", pool="0xowned", current=205.0, capital=200.0, unclaimed=0.75, realised=0.0, gas=0.0)
    store.upsert_position(p)
    snap = _verified_snapshot("0xowned")
    snap["market"] = {"pool_address": "0xowned", "fee_tier_bps": 1.0}
    store.save_position_snapshot("p4", snap)
    store.set_setting("fees:tracker:p4", {
        "age_days": 1.5, "fees_24h_usd": 0.50, "cumulative_earned_usd": 0.75,
        "opened_at": 1_800_000_000.0,
    })

    class ZeroVolumeMarket(_RangeMarket):
        def pool(self, chain, address):
            row = super().pool(chain, address)
            row.update({
                "chain": "ROBINHOOD_CHAIN", "pool_address": address, "pair": "WETH/USDG",
                "quote_token": {"address": "0x" + "2" * 40, "symbol": "USDG"},
                "volume_24h_usd": 0.0, "fee_tier_bps": 5.0,
            })
            return row

    out = recommend_profit_range(
        ZeroVolumeMarket(), store, "ROBINHOOD_CHAIN", "0xcandidate",
        horizon_days=7, capital=1000.0, sleeve="CORE_INCOME",
        compare_fee_tiers=False,
    )
    forecast = out["recommended_range"]["forecast"]
    assert forecast["expected_fees_usd"] > 0
    assert forecast["forecast_fee_apr_pct"] > 0
    assert forecast["fee_forecast_source"] == "SAME_PAIR_OWNED_FEE_PRIOR"
    assert out["owned_pair_fee_prior"]["confidence"] == "LOW"


def test_financial_truth_uses_simple_position_pnl_and_separate_realised_costs(tmp_path):
    store = Store(tmp_path / "truth.sqlite3")
    p = _position("p4")
    store.upsert_position(p)
    store.save_position_snapshot("p4", _verified_snapshot())
    store.set_setting("fees:tracker:p4", {
        "age_days": 2.0, "cumulative_earned_usd": 7.0,
        "fees_24h_usd": 2.0, "opened_at": 1_800_000_000.0,
    })
    truth = portfolio_financial_truth(store)
    assert truth["opening_wealth_verified_usd"] == pytest.approx(100.0)
    assert truth["current_open_strategy_wealth_usd"] == pytest.approx(117.0)
    assert truth["gross_open_pnl_incl_fees_usd"] == pytest.approx(17.0)
    assert truth["all_time_known_fees_usd"] == pytest.approx(7.0)
    assert truth["transaction_costs_usd"] == pytest.approx(1.0)
    assert truth["realised_gain_loss_usd"] == pytest.approx(1.0)


def test_forecast_snapshot_is_frozen_and_can_be_linked_to_discovered_position(tmp_path):
    store = Store(tmp_path / "forecast.sqlite3")
    result = {
        "chain": "ETHEREUM", "pool_address": "0xpool", "pair": "WETH/USDC",
        "sleeve": "CORE_INCOME", "horizon_days": 7.0, "capital_usd": 1000.0,
        "spot": 2700.0,
        "recommended_range": {
            "lower": 2500.0, "upper": 2850.0,
            "forecast": {
                "expected_fees_usd": 20.0, "expected_net_usd": 18.0,
                "forecast_fee_apr_pct": 104.3, "low_net_usd": 8.0, "high_net_usd": 25.0,
            },
        },
    }
    frozen = store.record_forecast_snapshot(result, model_version="v0.8.8")
    assert frozen["expected_fees_usd"] == pytest.approx(20.0)
    assert store.link_forecast_to_position(frozen["id"], "live:ETHEREUM:999") is True
    rows = store.list_forecast_snapshots()
    assert rows[0]["position_id"] == "live:ETHEREUM:999"
    assert rows[0]["payload"]["recommended_range"]["lower"] == pytest.approx(2500.0)


def test_opening_increase_liquidity_event_recovers_tick_without_archive_rpc():
    pytest.importorskip("web3")
    from web3 import Web3
    from lp_manager.live_positions import _opening_liquidity_event

    manager = "0x" + "a" * 40
    token_id = 12345
    tick_lower, tick_upper = -1200, 1200
    liquidity = 10**18
    per0, per1 = amounts_raw_per_liquidity(
        sqrt_price_x96=Q96, tick_lower=tick_lower, tick_upper=tick_upper
    )
    amount0 = int(per0 * liquidity)
    amount1 = int(per1 * liquidity)
    topic0 = "0x" + Web3.keccak(text="IncreaseLiquidity(uint256,uint128,uint256,uint256)").hex().removeprefix("0x")
    data = (
        int(liquidity).to_bytes(32, "big")
        + int(amount0).to_bytes(32, "big")
        + int(amount1).to_bytes(32, "big")
    )
    receipt = {
        "logs": [{
            "address": manager,
            "topics": [topic0, hex(token_id)],
            "data": "0x" + data.hex(),
        }]
    }
    out = _opening_liquidity_event(
        receipt, manager=manager, token_id=token_id,
        tick_lower=tick_lower, tick_upper=tick_upper, dec0=18, dec1=18,
    )
    assert out["liquidity"] == liquidity
    assert abs(out["reconstructed_tick"]) <= 2
    assert out["price_source"] == "OPENING_INCREASE_LIQUIDITY_EVENT"


def test_prepare_collect_and_close_propagate_position_chain_to_live_builder(tmp_path, monkeypatch):
    store = Store(tmp_path / "execution.sqlite3")
    p = _position("p4")
    store.upsert_position(p)
    snap = _verified_snapshot()
    snap.update({
        "wallet": "0x" + "b" * 40, "token_id": "123", "liquidity": "1000",
        "position_manager": "0x" + "c" * 40,
    })
    store.save_position_snapshot("p4", snap)
    settings = Settings(
        project_root=tmp_path, data_dir=tmp_path, database_path=tmp_path / "execution.sqlite3",
        execution_mode="build_only", legacy_root=tmp_path, currency="GBP", demo_seed=False,
    )
    seen = []

    def fake_collect(snapshot):
        seen.append(("collect", snapshot.get("chain")))
        return {"call": {"to": "0x" + "c" * 40, "data": "0x01", "value": "0x0"},
                "simulation": {"ok": True}, "manager": "0x" + "c" * 40, "token_id": "123"}

    def fake_close(snapshot):
        seen.append(("close", snapshot.get("chain")))
        return {"call": {"to": "0x" + "c" * 40, "data": "0x02", "value": "0x0"},
                "simulation": {"ok": True}, "manager": "0x" + "c" * 40, "token_id": "123",
                "slippage_bps": 100, "deadline": 1_900_000_000}

    monkeypatch.setattr("lp_manager.live_v3_builder.build_collect", fake_collect)
    monkeypatch.setattr("lp_manager.live_v3_builder.build_close", fake_close)

    service = ExecutionService(settings, store)
    row = store.get_position("p4")
    collect = service.prepare_collect(row)
    close = service.prepare_close(row)
    assert collect["ok"] is True
    assert close["ok"] is True
    assert seen == [("collect", "ROBINHOOD_CHAIN"), ("close", "ROBINHOOD_CHAIN")]


def test_wallet_picker_uses_family_deduplication_and_financial_ui_contract():
    js = (Path(__file__).parents[1] / "lp_manager" / "static" / "app.js").read_text(encoding="utf-8")
    assert "function walletFamily(" in js
    assert "const byFamily=new Map()" in js
    assert "P/L incl. fees" in js
    assert "All time fees" in js
    assert "Realised gain / loss" in js
