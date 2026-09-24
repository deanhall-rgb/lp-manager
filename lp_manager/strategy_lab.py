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


def analyse_live_pool(market, chain: str, address: str, *, sleeve: str, days: int, capital: float, target_monthly_pct: float = 10.0, pool_fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    sleeve=str(sleeve or "CORE_INCOME").upper()
    policy=policy_for(sleeve)
    requested_chain=str(chain or "").upper()
    requested_days=max(7,min(365,int(days)))
    history_timeframe="day" if requested_days > 45 else "hour"

    # On-chain metadata is authoritative for chain, token orientation and fee tier.
    onchain=read_v3_pool_metadata(requested_chain,address)
    pool_error=None
    # A Scout candidate already contains pool TVL/volume/token context. Reuse it
    # instead of spending another GeckoTerminal request immediately before OHLC.
    pool=dict(pool_fallback) if isinstance(pool_fallback,dict) and pool_fallback else None
    if pool is None:
        try:
            # The UI already knows the chain. Do not probe five more Gecko networks on a
            # transient 429; that only amplifies the rate limit. Test/simple adapters may
            # expose resolve_pool only, so retain that compatibility path.
            if hasattr(market,"pool"):
                pool=market.pool(requested_chain,address)
            else:
                resolved,pool=market.resolve_pool(requested_chain,address)
                requested_chain=str(resolved or requested_chain).upper()
        except Exception as exc:
            pool_error=str(exc)
    if pool is None:
        if onchain.get("ok"):
            pool=_pool_from_onchain(requested_chain,address,onchain,pool_fallback)
        elif pool_fallback:
            pool={**pool_fallback,"chain":requested_chain,"pool_address":address}
        else:
            raise RuntimeError(pool_error or str(onchain.get("error") or "Pool context unavailable"))
    elif onchain.get("ok"):
        pool={**pool,"fee_tier":onchain.get("fee_tier"),"fee_tier_bps":onchain.get("fee_tier_bps"),"tick_spacing":onchain.get("tick_spacing"),"onchain":onchain}
    chain=requested_chain

    history_provider="GECKOTERMINAL_POOL_OHLC"
    history_warning=None
    try:
        candles=market.ohlcv_days(chain,address,requested_days,timeframe=history_timeframe)
    except TypeError:  # compatibility with simple test/fake market adapters
        candles=market.ohlcv_days(chain,address,requested_days)
        history_timeframe="hour"
    except Exception as exc:
        history_warning=str(exc)
        candles=[]
    minimum_samples=20 if history_timeframe=="day" else 24
    if len(candles) < minimum_samples and hasattr(market,"alchemy_pool_history") and onchain.get("ok"):
        fallback=market.alchemy_pool_history(chain,onchain,requested_days,timeframe=history_timeframe)
        if len(fallback) >= minimum_samples:
            candles=fallback
            history_provider="ALCHEMY_TOKEN_PRICE_FALLBACK"
    if len(candles) < minimum_samples:
        detail=f"Only {len(candles)} historical {history_timeframe} candles available"
        if history_warning: detail += f"; primary provider: {history_warning}"
        raise ValueError(detail)

    spot=_f(candles[-1].get("close")) or _f((onchain.get("price_lens") or {}).get("current")) or _f(pool.get("base_token_price_usd"))
    regime_rows=candles
    regime_cpd=1 if history_timeframe=="day" else 24
    if history_timeframe=="day":
        try:
            recent=market.ohlcv_days(chain,address,min(30,requested_days),timeframe="hour")
            if len(recent)>=48:
                regime_rows=recent; regime_cpd=24
        except Exception:
            if hasattr(market,"alchemy_pool_history") and onchain.get("ok"):
                recent=market.alchemy_pool_history(chain,onchain,min(30,requested_days),timeframe="hour")
                if len(recent)>=48:
                    regime_rows=recent; regime_cpd=24
    regime=analyse_regime(regime_rows,candles_per_day=regime_cpd)

    desired=_f(regime.get("range_skew_pct"))
    if sleeve=="CORE_INCOME":
        skews=sorted(set([-10,-5,0,5,10,round(desired/5)*5]))
    else:
        skews=sorted(set([-6,-3,0,3,6,round(desired/3)*3]))
    ranked=rank_range_candidates(candles,spot,sleeve=sleeve,skews_pct=skews)
    raw=[_range_view(row,rank=i+1,regime=regime,pool=pool,capital=capital,history_days=requested_days) for i,row in enumerate(ranked[:16])]
    raw.sort(key=lambda r:r["score"],reverse=True)
    recommendations=[{**row,"rank":i} for i,row in enumerate(raw[:5],start=1)]
    warmup=min(max(24,len(candles)//4),max(24,len(candles)-24))
    policy_replay=run_replay(candles,sleeve=sleeve,pair=str(pool.get("pair") or "POOL"),chain=chain,protocol=str(pool.get("protocol") or "UNISWAP_V3"),warmup_candles=warmup,initial_capital=capital,pool_context=pool,target_monthly_pct=target_monthly_pct)
    evaluation=preliminary_pool_evaluation(pool)
    best=recommendations[0]
    reasons=[]
    if best["active_time_pct"] >= policy.desired_in_range_probability: reasons.append("Historical active time clears the sleeve durability target")
    if best["longest_oor_hours"] <= 24: reasons.append("Historical out-of-range periods were generally short")
    if evaluation.get("preferred_sleeve") == sleeve: reasons.append("Live pool-quality pre-score agrees with the selected sleeve")
    reasons.extend(regime.get("reasons") or [])
    if history_provider!="GECKOTERMINAL_POOL_OHLC": reasons.append("Historical range durability uses Alchemy token-price points because pool OHLC was unavailable/rate-limited")
    if best.get("economics",{}).get("estimated_operating_net_month_usd") is not None:
        reasons.append(f"Operating economics estimate: ${best['economics']['estimated_operating_net_month_usd']:,.2f} net/month on ${capital:,.0f} capital")
    if not reasons: reasons.append("Candidate is testable, but evidence does not yet justify automatic approval")
    econ=best.get("economics") or {}
    target_month_usd=max(0.0,float(capital))*max(0.0,float(target_monthly_pct))/100.0
    operating_month=_f(econ.get("estimated_operating_net_month_usd",econ.get("estimated_net_month_usd")))
    fee_month=_f((econ.get("estimated_fee_income") or {}).get("monthly"))
    shortfall=max(0.0,target_month_usd-operating_month)
    attainment=(operating_month/target_month_usd*100.0) if target_month_usd>0 else 0.0
    required_daily=(target_month_usd+_f(econ.get("estimated_lifecycle_cost_month_usd")))/30.4375 if target_month_usd>0 else 0.0
    target_diagnostics={
        "target_monthly_pct":float(target_monthly_pct),"target_month_usd":round(target_month_usd,2),
        "estimated_fee_month_usd":round(fee_month,2),"estimated_operating_net_month_usd":round(operating_month,2),
        "shortfall_usd":round(shortfall,2),"attainment_pct":round(attainment,1),"target_clears":bool(target_month_usd>0 and operating_month>=target_month_usd),
        "required_fee_day_usd":round(required_daily,2),"estimated_fee_day_usd":round(_f((econ.get("estimated_fee_income") or {}).get("daily")),2),
        "fee_share_method":econ.get("fee_share_method"),"persistence_haircut_factor":econ.get("persistence_haircut_factor"),
        "volume_quality":econ.get("volume_quality") or {},"confidence":econ.get("confidence"),
    }
    targets=performance_targets(capital=capital,target_monthly_pct=target_monthly_pct,actual_today=_f((econ.get("estimated_fee_income") or {}).get("daily")),actual_7d=_f((econ.get("estimated_fee_income") or {}).get("weekly")),actual_30d=max(0.0,operating_month))
    return {
        "pool":pool,"sleeve":sleeve,"capital":float(capital),"days":requested_days,"spot":spot,"history_samples":len(candles),
        "history_source":{"provider":history_provider,"timeframe":history_timeframe,"samples":len(candles),"requested_days":requested_days,"regime_samples":len(regime_rows),"warning":history_warning},
        "evaluation":evaluation,"policy":policy.to_dict(),"regime":regime,"recommendations":recommendations,
        "price_lens":pool_price_lens(pool,current=spot),
        "price_series":[{"timestamp":c.get("timestamp"),"close":c.get("close")} for c in candles[-240:]],
        "recommended_range":best,"economics":econ,"target_comparison":targets,"target_diagnostics":target_diagnostics,"reasons":reasons,"policy_replay":{
            "id":policy_replay.get("id"),"summary":policy_replay.get("summary") or {},"range_selection":policy_replay.get("range_selection") or {},
            "economics":policy_replay.get("economics") or {},"no_lookahead":policy_replay.get("no_lookahead"),
        },
        "economics_note":"Fee/P&L values remain estimates unless exact fee-growth data is available. Alchemy fallback history contains price points, not pool OHLC or pool volume; that limitation is shown explicitly.",
    }
