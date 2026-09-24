from __future__ import annotations

from typing import Any

from .asset_lens import pool_price_lens
from .economics_engine import estimate_lp_economics
from .live_scout import preliminary_pool_evaluation
from .market_regime import analyse_regime
from .portfolio_policy import policy_for
from .pool_chain import read_v3_pool_metadata
from .range_lab import rank_range_candidates
from .replay import run_replay
from .targets import performance_targets


def _f(v: Any, default: float = 0.0) -> float:
    try: return float(v)
    except Exception: return default


def _range_view(row: dict[str, Any], *, rank: int, regime: dict[str, Any], pool: dict[str, Any], capital: float, history_days: int) -> dict[str, Any]:
    c=row.get("candidate") or {}; a=row.get("analysis") or {}
    interventions=max(0, int(a.get("excursions") or 0))
    per_month = interventions * (30.4375 / max(1.0, float(history_days)))
    economics=estimate_lp_economics(
        pool,
        capital=capital,
        active_time_pct=_f(a.get("active_time_pct")),
        width_pct=_f(c.get("width_pct"),25.0),
        regime=regime,
        expected_interventions_per_month=per_month,
        lifecycle_cost_per_intervention=1.5,
        lower_price=_f(c.get("lower")),
        upper_price=_f(c.get("upper")),
    )
    hist_score=_f(row.get("range_score"))
    desired_skew=_f(regime.get("range_skew_pct"))
    alignment=max(0.0,100.0-min(100.0,abs(_f(c.get("skew_pct"))-desired_skew)*4.0))
    net_pct=_f(economics.get("estimated_net_month_pct"))
    economics_score=max(0.0,min(100.0,45.0+net_pct*3.0)) if economics.get("mode")!="INSUFFICIENT_DATA" else 30.0
    # Historical evidence remains dominant, but current market direction and
    # economics can move a candidate materially. This prevents stale historical
    # symmetry from winning during a strong breakout/trend.
    composite=0.58*hist_score+0.25*alignment+0.17*economics_score
    if str(regime.get("breakout_state") or "") == "UPSIDE_BREAKOUT" and _f(c.get("skew_pct")) > 0:
        composite += min(5.0, _f(c.get("skew_pct")) * 0.35)
    elif str(regime.get("breakout_state") or "") == "DOWNSIDE_BREAKDOWN" and _f(c.get("skew_pct")) < 0:
        composite += min(5.0, abs(_f(c.get("skew_pct"))) * 0.35)
    return {
        "rank":rank,
        "lower":_f(c.get("lower")),"upper":_f(c.get("upper")),"center":_f(c.get("center")),
        "width_pct":_f(c.get("width_pct")),"skew_pct":_f(c.get("skew_pct")),
        "historical_score":round(hist_score,1),"regime_alignment_score":round(alignment,1),"score":round(composite,1),
        "active_time_pct":_f(a.get("active_time_pct")),"volume_capture_pct":_f(a.get("volume_capture_pct")),
        "strict_survival_pct":_f(a.get("strict_horizon_survival_pct")),"average_horizon_activity_pct":_f(a.get("average_horizon_activity_pct")),
        "excursions":interventions,"estimated_interventions_month":round(per_month,2),"reentries":int(a.get("reentries") or 0),"longest_oor_hours":_f(a.get("longest_out_of_range_hours")),
        "sleep_score":round(max(0.0,min(100.0,_f(a.get("durability_score"))-min(25.0,interventions*1.5))),1),
        "economics":economics,
        "price_lens":pool_price_lens(pool,lower=_f(c.get("lower")),upper=_f(c.get("upper")),current=_f(pool.get("base_token_price_usd"))),
    }


def _pool_from_onchain(chain: str, address: str, onchain: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build enough pool context for Strategy Lab even when market-data lookup is degraded."""
    base=dict(fallback or {})
    lens=dict(onchain.get("price_lens") or {})
    unit_label=str(lens.get("unit_label") or "")
    token0=dict(onchain.get("token0") or {}); token1=dict(onchain.get("token1") or {})
    by_symbol={str(token0.get("symbol") or "").upper():token0,str(token1.get("symbol") or "").upper():token1}
    if " per " in unit_label:
        quote_sym,base_sym=[x.strip() for x in unit_label.split(" per ",1)]
    else:
        base_sym=str(token0.get("symbol") or "TOKEN0"); quote_sym=str(token1.get("symbol") or "TOKEN1")
    # On-chain execution orientation wins over provider "base/quote" naming.
    # A USDG/WETH provider row must become WETH/USDG when the human lens is
    # "USDG per WETH", otherwise token USD (~1) can leak into pool-price maths.
    base_token=dict(by_symbol.get(base_sym.upper()) or base.get("base_token") or {"symbol":base_sym})
    quote_token=dict(by_symbol.get(quote_sym.upper()) or base.get("quote_token") or {"symbol":quote_sym})
    current=_f(lens.get("current"))
    result={
        **base,
        "chain":chain.upper(),"protocol":"UNISWAP_V3","pool_address":address,
        "pair":f"{base_sym}/{quote_sym}","base_token":base_token,"quote_token":quote_token,
        "fee_tier":onchain.get("fee_tier"),"fee_tier_bps":onchain.get("fee_tier_bps"),
        "tick_spacing":onchain.get("tick_spacing"),"onchain":onchain,
        "price_unit":lens.get("unit"),"price_unit_label":unit_label,
    }
    # When the quote is USD-pegged, the pool ratio is also a usable USD spot mark.
    if str(quote_sym).upper() in {"USDC","USDT","USDG","DAI","USDS","USDBC","FRAX","GHO"} and current>0:
        result.setdefault("base_token_price_usd",current)
        result.setdefault("quote_token_price_usd",1.0)
    return result


def analyse_live_pool(
    market, chain: str, address: str, *, sleeve: str, days: int, capital: float,
    target_monthly_pct: float = 10.0, pool_fallback: dict[str, Any] | None = None,
    store=None,
) -> dict[str, Any]:
    """V0.8.7 compatibility view over the unified profit/range engine.

    Strategy Lab and Profit Lab used to rank different geometries. They now share
    one economic decision engine; this function preserves the Strategy Lab API/UI
    contract while exposing the same selected pool, range and fee maths.
    """
    from .profit_engine import recommend_profit_range

    requested_days=max(1,min(90,int(days)))
    sleeve_u=str(sleeve or "CORE_INCOME").upper()
    onchain_override=read_v3_pool_metadata(str(chain or "").upper(),address)
    fallback_with_onchain=dict(pool_fallback or {})
    if onchain_override.get("ok"):
        fallback_with_onchain["onchain"]=onchain_override
    result=recommend_profit_range(
        market,store=(store or _NullCalibrationStore()),chain=str(chain or "").upper(),address=address,
        horizon_days=requested_days,capital=max(1.0,float(capital)),sleeve=sleeve_u,
        monthly_target_pct=max(0.0,float(target_monthly_pct)),
        history_days=max(30,min(180,requested_days*6)),
        pool_fallback=(fallback_with_onchain or pool_fallback),compare_fee_tiers=True,
    )
    pool=dict(result.get("pool") or {})
    pool.setdefault("chain",result.get("chain"))
    pool.setdefault("pool_address",result.get("pool_address"))
    policy=policy_for(sleeve_u)

    best=result.get("recommended_range") or {}
    candidates=result.get("range_candidates") or []
    seen=set()
    picked=[]
    for row in [best,*candidates]:
        key=(round(_f(row.get("lower")),8),round(_f(row.get("upper")),8))
        if key in seen or key==(0.0,0.0):
            continue
        seen.add(key); picked.append(row)
        if len(picked)>=5:
            break

    def view(row: dict[str,Any], rank: int) -> dict[str,Any]:
        a=row.get("analysis") or {}
        wf=row.get("walk_forward") or {}
        excursions=int(_f(a.get("excursions")))
        active=_f(a.get("average_horizon_activity_pct"),_f(a.get("active_time_pct")))
        return {
            "rank":rank,
            "lower":_f(row.get("lower")),"upper":_f(row.get("upper")),"center":_f(row.get("center")),
            "width_pct":_f(row.get("width_pct")),"skew_pct":_f(row.get("skew_pct")),
            "historical_score":_f(row.get("profit_score")),"regime_alignment_score":_f(row.get("regime_alignment_score")),
            "score":_f(row.get("profit_score")),
            "active_time_pct":active,
            "volume_capture_pct":_f(a.get("volume_capture_pct")),
            "strict_survival_pct":_f(a.get("strict_horizon_survival_pct")),
            "average_horizon_activity_pct":active,
            "excursions":excursions,
            "estimated_interventions_month":round(excursions*(30.4375/max(1.0,float(result.get("evidence",{}).get("history_days") or requested_days))),2),
            "reentries":int(_f(a.get("reentries"))),
            "longest_oor_hours":_f(a.get("longest_out_of_range_hours")),
            "sleep_score":round(max(0.0,100.0-min(80.0,excursions*4.0)),1),
            "economics":row.get("economics") or {},
            "forecast":row.get("forecast") or {},
            "walk_forward":wf,
            "inventory_outcomes":row.get("inventory_outcomes") or {},
            "selection_evidence":row.get("selection_evidence") or {},
            "price_lens":row.get("price_lens") or result.get("price_lens") or {},
        }

    recommendations=[view(row,i+1) for i,row in enumerate(picked)]
    best_view=recommendations[0] if recommendations else view(best,1)
    forecast=best.get("forecast") or {}
    fee_day=_f(forecast.get("fee_day_current_calibrated_usd"))
    horizon_net=_f(forecast.get("expected_net_usd"))
    target_horizon=max(0.0,float(capital))*max(0.0,float(target_monthly_pct))/100.0*requested_days/30.4375
    target_diag={
        "target_monthly_pct":float(target_monthly_pct),
        "target_month_usd":round(max(0.0,float(capital))*max(0.0,float(target_monthly_pct))/100.0,2),
        "estimated_fee_month_usd":round(fee_day*30.4375,2),
        "estimated_operating_net_month_usd":round(horizon_net/requested_days*30.4375,2) if requested_days else 0.0,
        "shortfall_usd":round(max(0.0,target_horizon-horizon_net),2),
        "attainment_pct":round(horizon_net/target_horizon*100.0,1) if target_horizon>0 else 0.0,
        "target_clears":bool(target_horizon>0 and horizon_net>=target_horizon),
        "required_fee_day_usd":round(target_horizon/requested_days,2) if requested_days else 0.0,
        "estimated_fee_day_usd":round(fee_day,2),
        "fee_share_method":(best.get("economics") or {}).get("fee_share_method"),
        "persistence_haircut_factor":forecast.get("horizon_persistence_factor"),
        "volume_quality":(best.get("economics") or {}).get("volume_quality") or {},
        "confidence":result.get("confidence"),
    }
    targets=performance_targets(
        capital=float(capital),target_monthly_pct=float(target_monthly_pct),
        actual_today=fee_day,actual_7d=fee_day*7.0,
        actual_30d=(horizon_net/requested_days*30.4375 if requested_days else 0.0),
    )
    wf=best.get("walk_forward") or {}
    evaluation=preliminary_pool_evaluation(pool)
    reasons=list(result.get("why") or [])
    selection=result.get("pool_selection") or {}
    if selection.get("explanation"):
        reasons.append(str(selection["explanation"]))
    return {
        "pool":pool,
        "sleeve":sleeve_u,
        "capital":float(capital),
        "days":requested_days,
        "spot":result.get("spot"),
        "history_samples":int((result.get("evidence") or {}).get("history_samples") or 0),
        "history_source":{
            "provider":(result.get("evidence") or {}).get("history_provider"),
            "timeframe":"hour" if requested_days<=45 else "day",
            "samples":int((result.get("evidence") or {}).get("history_samples") or 0),
            "requested_days":int((result.get("evidence") or {}).get("history_days") or requested_days),
            "warning":(result.get("evidence") or {}).get("provider_warning"),
        },
        "evaluation":evaluation,
        "policy":policy.to_dict(),
        "regime":result.get("regime") or {},
        "recommendations":recommendations,
        "price_lens":result.get("price_lens") or {},
        "price_series":result.get("price_series") or [],
        "recommended_range":best_view,
        "economics":best.get("economics") or {},
        "forecast":forecast,
        "target_comparison":targets,
        "target_diagnostics":target_diag,
        "reasons":reasons,
        "pool_comparison":result.get("pool_comparison") or [],
        "pool_selection":selection,
        "fee_calibration":result.get("fee_calibration") or {},
        "policy_replay":{
            "id":None,
            "summary":{
                "period_days":requested_days,
                "monitor_checks":int((result.get("evidence") or {}).get("history_samples") or 0),
                "strategy_reviews":int((wf.get("windows") or 0)),
                "ai_wakes":0,
                "material_events":int(_f((wf.get("holdout") or {}).get("excursions_median"))),
            },
            "range_selection":{"source":"UNIFIED_PROFIT_ENGINE"},
            "economics":best.get("economics") or {},
            "no_lookahead":True,
        },
        "economics_note":"V0.8.7 Strategy Lab is a compatibility view of the unified profit engine. Forecast fee APR, observed fee evidence and LP-vs-HODL accounting remain separate metrics.",
        "data_status":"LIVE_OR_FRESH_HISTORY",
    }


class _NullCalibrationStore:
    """Strategy compatibility path when no Store was historically supplied.

    API Profit Lab passes the real Store and therefore receives owned-position
    calibration. Strategy Lab keeps identical deterministic range logic without
    inventing live calibration data.
    """
    def list_positions(self, status=None):
        return []
    def get_position_snapshot(self, position_id):
        return {}
    def get_setting(self, key, default=None):
        return default
