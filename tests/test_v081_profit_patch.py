from pathlib import Path

from lp_manager.db import Store
from lp_manager.economics_engine import estimate_lp_economics, volume_quality
from lp_manager.historical_import import import_delta_pool_history
from lp_manager.legacy_bridge import import_campaign_ledger
from lp_manager.portfolio_advisor import rank_opportunities
from lp_manager.v3_math import tick_to_sqrt_price_x96, quote_paired_amount


def test_economics_does_not_book_divergence_risk_as_monthly_cash_loss():
    pool={"pair":"WETH/USDC","tvl_usd":20_000_000,"volume_24h_usd":12_000_000,"fee_tier_bps":5,"pool_created_at":"2020-01-01T00:00:00Z"}
    e=estimate_lp_economics(pool,capital=1000,active_time_pct=85,width_pct=40,regime={"realised_volatility_pct":25,"score":80})
    assert e["estimated_operating_net_month_usd"] == e["estimated_net_month_usd"]
    assert e["estimated_operating_net_month_usd"] > e["risk_adjusted_planning_month_usd"]
    assert e["lp_vs_hodl_risk_allowance_month_usd"] > 0


def test_extreme_activity_is_haircut_and_cannot_self_validate():
    pool={"tvl_usd":10_000,"volume_24h_usd":800_000,"price_change_percentage":{"h24":900},"pool_created_at":"2026-09-23T00:00:00Z"}
    q=volume_quality(pool)
    assert q["factor"] < 0.20
    assert q["flags"]


def test_execution_quote_auto_balances_other_side_for_in_range_weth_usdc():
    meta={
        "token0":{"decimals":6,"symbol":"USDC"},"token1":{"decimals":18,"symbol":"WETH"},
        "tick_spacing":10,"current_tick":197445,"sqrt_price_x96":int(tick_to_sqrt_price_x96(197445)),
        "price_lens":{"inverted":True,"unit":"USDC_PER_WETH","current":2663.8},
    }
    q=quote_paired_amount(meta,lower=2237.36,upper=3356.04,known_side=0,known_amount=30)
    assert q["amount0"] == 30
    assert q["amount1"] > 0
    assert q["tick_lower"] < q["current_tick"] < q["tick_upper"]


def test_advisor_returns_near_miss_instead_of_blank():
    rows=[{"pair":"WETH/USDC","chain":"BASE","pool_address":"0x"+"1"*40,"sleeve":"CORE_INCOME",
           "evaluation":{"core_pre_score":50,"tactical_pre_score":45,"risk_core":{"eligible":True,"blockers":[]}},
           "economics":{"capital_usd":1000,"estimated_net_month_pct":1,"estimated_operating_net_month_usd":10,"mode":"ESTIMATED_FROM_VOLUME_TVL_RANGE","volume_quality":{"factor":1}},
           "regime":{"confidence":50}}]
    r=rank_opportunities(rows,available_capital=1000,reserve_pct=10,sleeve_filter="CORE_INCOME")
    assert not r["allocations"]
    assert r["near_misses"]
    assert r["near_misses"][0]["reject_reasons"]


def _history_payload():
    return {"metadata":{"chain_id":4663,"pool":"0xD64FbdA67E1015dF43Fa5e49F02cA844729E5F94","token0":{"symbol":"WETH","decimals":18},"token1":{"symbol":"DELTA","decimals":18},"fee_pips":10000},
            "positions":{"P1":{"token_id":1206967,"open_timestamp":1,"tick_lower":119000,"tick_upper":124000,"opening_tick_reconstructed":120888,"opening_delta_per_weth_reconstructed":177761,"actual_initial_weth":.05,"actual_initial_delta":5750,"status":"closed","elapsed_seconds":1000,"range_utilisation_pct":99,"fee_est_weth_from_non_crossing_active_swaps":.019}}}


def test_authoritative_history_archives_legacy_delta_identity(tmp_path: Path):
    store=Store(tmp_path/"db.sqlite")
    import_delta_pool_history(store,_history_payload())
    root=tmp_path/"legacy"; root.mkdir()
    (root/"campaign_ledger.json").write_text('{"campaigns":{"old":{"status":"CLOSED","position_snapshot":{"token0_symbol":"WETH","token1_symbol":"DELTA","token_id":999,"network":"ROBINHOOD_CHAIN"}}}}')
    import_campaign_ledger(store,root)
    rows=store.list_positions()
    real=[r for r in rows if str(r.get("token_id"))=="1206967"][0]
    old=[r for r in rows if str(r.get("token_id"))=="999"][0]
    assert real["display_name"]=="DELTA LP1"
    assert old["monitoring_class"]=="ARCHIVED_SUPERSEDED"
    assert old["display_name"].startswith("Legacy DELTA campaign")
