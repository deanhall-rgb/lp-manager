from __future__ import annotations

import math
from typing import Any


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x=float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _norm(values: list[float], value: float) -> float:
    vals=[x for x in values if math.isfinite(x)]
    if not vals:
        return 50.0
    lo=min(vals); hi=max(vals)
    if hi-lo < 1e-12:
        return 50.0
    return max(0.0,min(100.0,(value-lo)/(hi-lo)*100.0))


def rank_capital_candidates(
    rows: list[dict[str, Any]],
    *,
    capital_usd: float,
    open_positions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Transparent cross-chain score for the next unit of deployable capital.

    This is not a substitute for each pool's deep range analysis. It compares the
    completed analyses on the same capital/horizon basis using expected cash profit,
    downside case, fee efficiency, confidence, intervention burden and existing
    concentration. Every component is returned so the score is auditable.
    """
    capital=max(1e-9,_f(capital_usd,1.0))
    open_positions=open_positions or []
    total_existing=sum(max(0.0,_f(p.get("current_value"))) for p in open_positions)

    exp_pcts=[_f(r.get("expected_net_usd"))/capital*100.0 for r in rows]
    low_pcts=[_f(r.get("low_net_usd"))/capital*100.0 for r in rows]
    fee_eff=[
        (_f(r.get("expected_net_usd"))/max(_f(r.get("expected_fees_usd")),1e-9))*100.0
        if _f(r.get("expected_fees_usd"))>0 else 0.0
        for r in rows
    ]

    out=[]
    confidence_map={"HIGH":100.0,"MODERATE_TO_HIGH":85.0,"MODERATE":72.0,"LOW":45.0,"UNAVAILABLE":30.0}
    for row,exp_pct,low_pct,eff in zip(rows,exp_pcts,low_pcts,fee_eff):
        pair=str(row.get("pair") or "").upper()
        chain=str(row.get("chain") or "").upper()
        same_pair=sum(
            max(0.0,_f(p.get("current_value")))
            for p in open_positions
            if str(p.get("pair") or "").upper()==pair
        )
        same_chain=sum(
            max(0.0,_f(p.get("current_value")))
            for p in open_positions
            if str(p.get("chain") or "").upper()==chain
        )
        pair_conc=(same_pair/total_existing*100.0) if total_existing>0 else 0.0
        chain_conc=(same_chain/total_existing*100.0) if total_existing>0 else 0.0
        diversification=max(0.0,100.0-(0.70*pair_conc+0.30*chain_conc))

        rec=row.get("recommendation") or {}
        best=rec.get("recommended_range") or {}
        analysis=best.get("analysis") or {}
        forecast=best.get("forecast") or {}
        interventions=max(0.0,_f(analysis.get("excursions")))
        intervention_cost=max(0.0,_f(forecast.get("expected_intervention_cost_usd")))
        intervention_quality=max(0.0,100.0-min(100.0,interventions*8.0+intervention_cost/capital*100.0*12.0))
        conf=confidence_map.get(str(row.get("confidence") or "").upper(),50.0)

        components={
            "expected_net":round(_norm(exp_pcts,exp_pct),1),
            "downside_case":round(_norm(low_pcts,low_pct),1),
            "fee_efficiency":round(max(0.0,min(100.0,eff)),1),
            "confidence":round(conf,1),
            "intervention_quality":round(intervention_quality,1),
            "diversification":round(diversification,1),
        }
        score=(
            0.40*components["expected_net"]
            +0.20*components["downside_case"]
            +0.15*components["fee_efficiency"]
            +0.10*components["confidence"]
            +0.075*components["intervention_quality"]
            +0.075*components["diversification"]
        )
        out.append({
            **row,
            "allocation_score":round(score,1),
            "allocation_evidence":{
                **components,
                "expected_net_pct":round(exp_pct,4),
                "low_net_pct":round(low_pct,4),
                "fee_efficiency_pct":round(eff,2),
                "existing_pair_concentration_pct":round(pair_conc,2),
                "existing_chain_concentration_pct":round(chain_conc,2),
                "historical_excursions":round(interventions,2),
                "expected_intervention_cost_usd":round(intervention_cost,2),
                "formula":"40% expected net + 20% downside + 15% fee efficiency + 10% confidence + 7.5% intervention quality + 7.5% diversification",
            },
        })

    return sorted(
        out,
        key=lambda r:(
            _f(r.get("allocation_score")),
            _f(r.get("expected_net_usd")),
        ),
        reverse=True,
    )
