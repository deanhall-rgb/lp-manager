from __future__ import annotations

from lp_manager.campaign_decision import build_campaign_decision
from lp_manager.db import Store
from lp_manager.models import Position


def _position(pid: str, current: float, lower: float = 90.0, upper: float = 120.0) -> Position:
    return Position(
        id=pid,protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",pair="WETH/DELTA",
        status="OPEN",lower_price=lower,upper_price=upper,current_price=current,
        capital_value=270.0,current_value=220.0,unclaimed_fees=10.0,
        fees_today=0.0,fees_7d=0.0,fees_30d=0.0,realised_fees=0.0,
        estimated_il=0.0,gas_costs=1.0,apr_current=100.0,apr_7d=100.0,
        opened_at=1000.0,token_id=pid.split(":")[-1],source="live_chain",
        strategy_sleeve="TACTICAL_CAMPAIGN",target_hold_days=3.0,
        display_name="P7 · WETH/DELTA",pool_address="0xpool",
        cost_basis_quality="ONCHAIN_MINT_RECONSTRUCTED",
    )


def _snapshot() -> dict:
    return {
        "live":True,
        "token0":{"symbol":"WETH","address":"0xweth","amount":0.0,"price_usd":2500.0},
        "token1":{"symbol":"DELTA","address":"0xdelta","amount":15000.0,"price_usd":0.015},
        "entry_evidence":{
            "basis_complete":True,"entry_value_usd":270.0,
            "token0_amount":0.075,"token1_amount":6136.0,
            "token0_price_usd":2500.0,"token1_price_usd":0.0135,
            "opened_at":1000.0,"transaction_hash":"0xopen",
            "quality":"ONCHAIN_MINT_RECONSTRUCTED",
        },
    }


def _profit(expected_net: float, break_even: float, expected_fees: float = 7.0) -> dict:
    return {
        "confidence":"MODERATE",
        "recommended_range":{
            "lower":100.0,"upper":140.0,
            "price_lens":{"unit":"DELTA_PER_WETH"},
            "forecast":{
                "expected_fees_usd":expected_fees,
                "expected_intervention_cost_usd":1.0,
                "expected_net_usd":expected_net,
                "forecast_fee_apr_pct":120.0,
                "fee_break_even_days_one_intervention":break_even,
            },
        },
    }


def _store(tmp_path, current: float) -> tuple[Store, str]:
    store=Store(tmp_path/"campaign_decision.sqlite3")
    pid="live:ROBINHOOD_CHAIN:1290067"
    p=_position(pid,current)
    store.upsert_position(p)
    store.save_position_snapshot(pid,_snapshot())
    store.set_setting(f"fees:tracker:{pid}",{
        "age_days":3.0,
        "cumulative_earned_usd":10.0,
        "fees_24h_usd":4.0,
    })
    return store,pid


def test_out_of_range_positive_redeployment_recommends_rerange(tmp_path):
    store,pid=_store(tmp_path,130.0)
    out=build_campaign_decision(
        store,"campaign:ROBINHOOD_CHAIN:DELTA",
        profit_result=_profit(6.0,1.0),position_id=pid,
    )
    assert out["current_position"]["range_state"] == "OUT_ABOVE"
    assert out["recommended_action"] == "RE_RANGE"
    assert out["rebalance_capital_usd"] == 230.0
    assert out["profit_lab_prefill"]["horizon_days"] == 3.0
    actions={x["action"]:x for x in out["options"]}
    assert actions["RE_RANGE"]["recommended"] is True
    assert actions["RE_RANGE"]["expected_net_usd"] == 6.0
    assert actions["HODL_WAIT"]["directional_return_usd"] is None


def test_out_of_range_weak_redeployment_recommends_hodl_wait(tmp_path):
    store,pid=_store(tmp_path,130.0)
    out=build_campaign_decision(
        store,"campaign:ROBINHOOD_CHAIN:DELTA",
        profit_result=_profit(0.10,4.0),position_id=pid,
    )
    assert out["recommended_action"] == "HODL_WAIT"
    hold=next(x for x in out["options"] if x["action"]=="HODL_WAIT")
    assert hold["recommended"] is True
    assert "automatic re-entry" in hold["summary"]


def test_in_range_profitable_current_lp_does_not_churn_for_weaker_rerange(tmp_path):
    store,pid=_store(tmp_path,105.0)
    out=build_campaign_decision(
        store,"campaign:ROBINHOOD_CHAIN:DELTA",
        profit_result=_profit(8.0,1.0,expected_fees=9.0),position_id=pid,
    )
    assert out["current_position"]["range_state"] == "IN_RANGE"
    # Current mature pace is £4/day × 3 days = £12, so an £8 fresh-range net
    # must not trigger unnecessary close/reopen churn.
    keep=next(x for x in out["options"] if x["action"]=="KEEP")
    assert keep["expected_fees_usd"] == 12.0
    assert out["recommended_action"] == "KEEP"


def test_v092_ui_has_campaign_decision_and_safe_profit_handoff():
    from pathlib import Path
    js=(Path(__file__).parents[1]/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")
    assert "Campaign decision / rebalance" in js
    assert "Review next move" in js
    assert "Analyse proposed range in Profit Lab" in js
    assert "Do not mint the replacement from the same capital" in js
    assert "$$('[data-campaign-position]').forEach" in js
    assert "reviewCampaignDecision(p.campaign_id,id)" in js
