from __future__ import annotations

import time

from lp_manager.campaign_decision import build_campaign_decision
from lp_manager.campaign_sentiment import (
    asset_direction_from_ratio,
    execution_skew_pct,
    technical_fallback,
)
from lp_manager.db import Store
from lp_manager.models import Position
from lp_manager.profit_engine import _compatible_fee_tier_spot
from lp_manager.economics_engine import infer_fee_tier_bps


def _position(pid: str, current: float = 130.0) -> Position:
    return Position(
        id=pid,protocol="UNISWAP_V3",chain="ROBINHOOD_CHAIN",pair="WETH/DELTA",
        status="OPEN",lower_price=90.0,upper_price=120.0,current_price=current,
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


def _profit() -> dict:
    return {
        "confidence":"MODERATE",
        "recommended_range":{
            "lower":100.0,"upper":140.0,
            "price_lens":{"unit":"DELTA_PER_WETH"},
            "forecast":{
                "expected_fees_usd":7.0,
                "expected_intervention_cost_usd":1.0,
                "expected_net_usd":6.0,
                "forecast_fee_apr_pct":120.0,
                "fee_break_even_days_one_intervention":1.0,
            },
        },
    }


def test_ratio_direction_is_translated_to_campaign_asset():
    # DELTA_PER_WETH down means fewer DELTA buy one WETH: DELTA strengthened.
    assert asset_direction_from_ratio("BEARISH","DELTA_PER_WETH","DELTA") == "BULLISH"
    assert asset_direction_from_ratio("BULLISH","DELTA_PER_WETH","DELTA") == "BEARISH"
    assert asset_direction_from_ratio("BULLISH","DELTA_PER_WETH","WETH") == "BULLISH"

    tech=technical_fallback(
        "DELTA",
        {"direction":"BEARISH","confidence":70},
        {"unit":"DELTA_PER_WETH"},
    )
    assert tech["technical_signal"] == "BULLISH"
    assert tech["stance"] == "LEAN_BULLISH"


def test_fresh_thesis_skew_is_bounded_and_price_lens_aware():
    thesis={
        "asset_symbol":"DELTA",
        "stance":"BULLISH",
        "confidence":80,
        "hold_comfort":"COMFORTABLE",
        "generated_at":time.time(),
    }
    skew=execution_skew_pct(
        thesis,unit="DELTA_PER_WETH",asset_symbol="DELTA",sleeve="TACTICAL_CAMPAIGN"
    )
    assert -3.0 <= skew < 0.0

    # Same bullish asset as denominator moves the execution ratio upward.
    skew2=execution_skew_pct(
        {**thesis,"asset_symbol":"WETH"},
        unit="DELTA_PER_WETH",asset_symbol="WETH",sleeve="TACTICAL_CAMPAIGN",
    )
    assert 0.0 < skew2 <= 3.0


def test_strong_uncomfortable_thesis_can_override_positive_rerange(tmp_path):
    store=Store(tmp_path/"v093.sqlite3")
    pid="live:ROBINHOOD_CHAIN:1290067"
    store.upsert_position(_position(pid))
    store.save_position_snapshot(pid,_snapshot())
    store.set_setting(f"fees:tracker:{pid}",{
        "age_days":3.0,"cumulative_earned_usd":10.0,"fees_24h_usd":4.0,
    })
    store.set_setting("campaign:thesis:campaign:ROBINHOOD_CHAIN:DELTA",{
        "stance":"BEARISH","confidence":82.0,
        "hold_comfort":"UNCOMFORTABLE","hold_comfort_score":22.0,
        "generated_at":time.time(),
    })

    out=build_campaign_decision(
        store,"campaign:ROBINHOOD_CHAIN:DELTA",
        profit_result=_profit(),position_id=pid,
    )
    assert out["recommended_action"] == "EXIT"
    assert out["thesis_flags"]["strong_exit_caution"] is True
    exit_row=next(x for x in out["options"] if x["action"]=="EXIT")
    assert exit_row["recommended"] is True
    assert exit_row["status"] == "THESIS_CAUTION"


def test_explicit_fee_tier_bps_is_not_converted_twice():
    assert infer_fee_tier_bps({"fee_tier_bps":100.0}) == (100.0, "POOL_METADATA")
    assert infer_fee_tier_bps({"fee_bps":100.0}) == (100.0, "POOL_METADATA")
    assert infer_fee_tier_bps({"fee_tier":10000}) == (100.0, "POOL_METADATA")
    assert infer_fee_tier_bps({"fee_tier_bps":1.0}) == (1.0, "POOL_METADATA")


def test_fee_tier_cross_pool_guard_rejects_hookr_scale_anomaly():
    seed={"spot":340_222.0,"price_lens":{"unit":"HOOKR_PER_WETH","current":340_222.0}}
    sane={"spot":341_100.0,"price_lens":{"unit":"HOOKR_PER_WETH","current":341_100.0}}
    absurd={"spot":3.40222e35,"price_lens":{"unit":"HOOKR_PER_WETH","current":3.40222e35}}

    ok,reason=_compatible_fee_tier_spot(seed,sane)
    assert ok is True
    assert reason == ""

    ok,reason=_compatible_fee_tier_spot(seed,absurd)
    assert ok is False
    assert "divergence" in reason


def test_v093_ui_exposes_sentiment_and_campaign_profit_overlay():
    from pathlib import Path

    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'id="sentiment-section"' in html
    assert 'data-section="sentiment"' in html
    assert "Research sentiment" in html
    assert "function renderSentiment()" in js
    assert "function renderProfitThesisContext" in js
    assert "campaign_id:sameCampaign?profitDecisionContext.campaign_id:null" in js
    assert "Research sentiment / thesis" in js
    assert "technical skew" in js.lower()
