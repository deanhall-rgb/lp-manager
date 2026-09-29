from __future__ import annotations

import math
from typing import Any

from .campaign_accounting import campaign_by_id
from .portfolio_accounting import position_accounting
from .strategy import edge_risk
from .campaign_sentiment import thesis_decision_flags


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _current_fee_projection(tracker: dict[str,Any], horizon_days: float, in_range: bool) -> dict[str,Any]:
    age=max(0.0,_f(tracker.get("age_days")))
    cumulative=max(0.0,_f(tracker.get("cumulative_earned_usd")))
    fee24=max(0.0,_f(tracker.get("fees_24h_usd")))
    if not in_range:
        return {
            "expected_fees_usd":0.0,
            "daily_fee_pace_usd":0.0,
            "quality":"OUT_OF_RANGE_CURRENTLY_ZERO",
            "note":"The current LP is not earning concentrated-liquidity fees while out of range. Re-entry is possible but is not forecast here.",
        }
    if age>=1.0 and fee24>0:
        pace=fee24
        quality="MATURE_24H_OBSERVED_PACE" if age>=3 else "EARLY_24H_OBSERVED_PACE"
    elif age>0 and cumulative>0:
        pace=cumulative/max(age,1/24)
        quality="IMMATURE_SINCE_OPEN_PACE"
    else:
        pace=0.0
        quality="NO_MATURE_FEE_SAMPLE"
    return {
        "expected_fees_usd":round(pace*horizon_days,4),
        "daily_fee_pace_usd":round(pace,4),
        "quality":quality,
        "note":"This is a simple continuation of the observed fee pace, not a price-path forecast." if pace>0 else "No reliable current-position fee pace is available yet.",
    }


def build_campaign_decision(
    store,
    campaign_id: str,
    *,
    profit_result: dict[str,Any] | None = None,
    position_id: str | None = None,
    horizon_days: float | None = None,
    monthly_target_pct: float = 10.0,
    profit_error: str = "",
    campaign_context: dict[str,Any] | None = None,
) -> dict[str,Any]:
    campaign=dict(campaign_context or campaign_by_id(store,campaign_id) or {})
    if not campaign:
        raise ValueError("Campaign not found")
    thesis=store.get_setting(f"campaign:thesis:{campaign_id}",None)
    thesis_flags=thesis_decision_flags(thesis)

    raw_positions={str(p.get("id") or ""):p for p in store.list_positions()}
    open_ids=[str(p.get("id") or "") for p in campaign.get("positions",[]) if str(p.get("status") or "").upper()=="OPEN"]
    if position_id:
        if position_id not in open_ids:
            raise ValueError("Selected position is not an open leg of this campaign")
        active_id=position_id
    else:
        ranked=sorted(
            [raw_positions[x] for x in open_ids if x in raw_positions],
            key=lambda p:_f(p.get("current_value")),
            reverse=True,
        )
        active_id=str(ranked[0].get("id") or "") if ranked else ""

    position=raw_positions.get(active_id) if active_id else None
    wallet=dict(campaign.get("wallet_inventory") or {})
    capital_context=dict(campaign.get("capital_accounting") or {})

    if not position:
        action="HODL_WAIT" if _f(wallet.get("balance"))>0 else "NO_ACTIVE_CAPITAL"
        return {
            "campaign_id":campaign_id,
            "campaign_label":campaign.get("label"),
            "generated_for_position_id":None,
            "recommended_action":action,
            "decision_confidence":"LIMITED",
            "headline":(
                f"{campaign.get('label')}: no open LP leg; campaign inventory is currently being held."
                if action=="HODL_WAIT"
                else f"{campaign.get('label')}: no open LP leg or attributed campaign inventory requires action."
            ),
            "rationale":(
                "No open LP can be re-ranged. The latest thesis is available below for the hold/exit decision."
                if thesis_flags.get("fresh")
                else "No open LP can be re-ranged. Research the campaign thesis before treating HODL or EXIT as a directional conclusion."
            ),
            "campaign":campaign,
            "campaign_summary":{
                "capital_invested_usd":capital_context.get("capital_invested_usd"),
                "marked_exposure_usd":capital_context.get("marked_exposure_usd"),
                "exposure_pnl_usd":capital_context.get("exposure_pnl_usd"),
                "exposure_return_pct":capital_context.get("exposure_return_pct"),
                "profit_cushion_usd":capital_context.get("profit_cushion_usd"),
                "capital_shortfall_usd":capital_context.get("capital_shortfall_usd"),
                "capital_state":capital_context.get("state"),
                "movement_count":capital_context.get("movement_count"),
                "known_campaign_pnl_usd":campaign.get("known_campaign_pnl_usd"),
                "lifetime_fees_usd":campaign.get("lifetime_fees_usd"),
                "transaction_costs_usd":campaign.get("transaction_costs_usd"),
                "wallet_inventory":wallet,
            },
            "thesis":thesis,
            "thesis_flags":thesis_flags,
            "options":[{
                "action":"HODL_WAIT","label":"HODL / WAIT","status":"CURRENT_STATE" if action=="HODL_WAIT" else "AVAILABLE",
                "expected_fees_usd":0.0,"directional_return_usd":None,
                "summary":(
                    f"Keep the campaign asset undeployed. Current thesis: {thesis_flags.get('stance')} / holding comfort {thesis_flags.get('hold_comfort')}."
                    if thesis_flags.get("fresh")
                    else "Keep the campaign asset undeployed only as a neutral current state; directional thesis has not been freshly researched."
                ),
            },{
                "action":"NEW_LP","label":"Open new LP","status":"AVAILABLE",
                "expected_fees_usd":None,"directional_return_usd":None,
                "summary":"Run Profit Lab as a fresh deployment rather than a rebalance.",
            },{
                "action":"EXIT","label":"Exit campaign","status":"REQUIRES_THESIS",
                "expected_fees_usd":0.0,"directional_return_usd":None,
                "summary":(
                    "Current fresh thesis flags the asset as uncomfortable to hold; review an exit rather than treating wallet inventory as harmless."
                    if thesis_flags.get("strong_exit_caution")
                    else "Swap out of the campaign asset only when thesis/holding-comfort evidence justifies giving up future exposure."
                ),
            }],
            "limitations":["No open LP leg exists.","Thesis is advisory evidence, not a guaranteed directional return forecast."],
        }

    snap=store.get_position_snapshot(active_id) or {}
    tracker=store.get_setting(f"fees:tracker:{active_id}",{}) or {}
    acct=position_accounting(position,snap,tracker)
    risk=edge_risk(position)
    state=str(risk.get("range_state") or "UNKNOWN")
    in_range=state=="IN_RANGE"
    horizon=max(1/24.0,_f(horizon_days if horizon_days is not None else position.get("target_hold_days"),3.0))
    sleeve=str(position.get("strategy_sleeve") or "TACTICAL_CAMPAIGN").upper()
    current_capital=max(0.0,_f(acct.get("strategy_wealth_usd") or acct.get("current_principal_usd") or position.get("current_value")))
    current_projection=_current_fee_projection(tracker,horizon,in_range)

    best=dict((profit_result or {}).get("recommended_range") or {})
    forecast=dict(best.get("forecast") or {})
    rerange_available=bool(best and forecast)
    rerange_net=_f(forecast.get("expected_net_usd")) if rerange_available else None
    rerange_fees=_f(forecast.get("expected_fees_usd")) if rerange_available else None
    intervention=_f(forecast.get("expected_intervention_cost_usd")) if rerange_available else None
    break_even=forecast.get("fee_break_even_days_one_intervention") if rerange_available else None
    break_even=_f(break_even,-1) if break_even is not None else None
    keep_fees=_f(current_projection.get("expected_fees_usd"))
    nearest=risk.get("nearest_edge_pct")
    nearest=_f(nearest,999.0) if nearest is not None else None
    confidence=str((profit_result or {}).get("confidence") or "UNAVAILABLE").upper()

    # Fee economics remain the primary deployment guardrail. A fresh v0.9.3
    # thesis can veto holding/redeployment when the asset is explicitly judged
    # uncomfortable to hold, or support waiting when a positive thesis remains.
    if in_range:
        rerange_beats_keep=bool(
            rerange_available
            and rerange_net is not None
            and rerange_net > max(0.25,keep_fees*1.15)
            and (break_even is None or break_even<0 or break_even<=horizon)
        )
        if str(risk.get("band") or "").upper()=="INTERVENE" and rerange_beats_keep:
            recommended="RE_RANGE"
            headline=f"{campaign.get('label')}: range edge is critical; compare a fresh range before fee production shuts off."
            rationale="The current position is still earning, but it is at an intervention edge and the fresh-range fee case clears the current continuation estimate by the configured hurdle."
        else:
            recommended="KEEP"
            headline=f"{campaign.get('label')}: keep the current LP for now."
            rationale="The position is still in range. v0.9.2 avoids paying close/reopen friction unless a near-edge fresh range has a materially better fee case."
    else:
        rerange_viable=bool(
            rerange_available
            and rerange_net is not None
            and rerange_net>0.25
            and (break_even is None or break_even<0 or break_even<=horizon)
        )
        if rerange_viable:
            recommended="RE_RANGE"
            headline=f"{campaign.get('label')}: current LP is out of range; re-range economics justify reviewing a redeployment."
            rationale="The current LP is earning no concentrated-liquidity fees. The fresh-range forecast is positive after its modelled intervention cost and reaches fee break-even inside the selected horizon."
        else:
            recommended="HODL_WAIT"
            headline=f"{campaign.get('label')}: wait rather than force a fee-negative rebalance."
            rationale="The current LP is out of range, but the available re-range evidence does not yet justify paying intervention friction. Leaving the one-sided NFT untouched can preserve automatic re-entry without another transaction."

    # Sentiment/thesis can change the management choice, but never manufactures
    # fee economics. Strong holding discomfort is the only thesis state allowed
    # to override a positive re-range recommendation.
    if thesis_flags.get("strong_exit_caution"):
        recommended="EXIT"
        headline=f"{campaign.get('label')}: fresh thesis says this is not an asset we are comfortable being left holding."
        rationale=(
            f"Fee economics are secondary while holding comfort is {thesis_flags.get('hold_comfort')} "
            f"at {thesis_flags.get('confidence')}% thesis confidence. Review closing the LP and campaign exposure rather than automatically re-ranging."
        )
    elif not in_range and thesis_flags.get("positive_hold_thesis"):
        if recommended=="RE_RANGE":
            rationale += " Fresh bullish/holding-comfort evidence supports remaining in the campaign while the new range is evaluated."
        else:
            headline=f"{campaign.get('label')}: bullish/acceptable holding thesis supports waiting rather than forcing a weak rebalance."
            rationale="The fresh thesis supports retaining the asset, while the available re-range economics do not yet justify another close/open cycle."

    # The transaction-traced campaign ledger is decision context, not a new
    # trading trigger. It makes capital preservation visible without allowing
    # sunk-cost or "house money" thinking to override the proven fee/range/thesis
    # guardrails.
    traced_capital=max(0.0,_f(capital_context.get("capital_invested_usd")))
    exposure_pnl=_f(capital_context.get("exposure_pnl_usd"))
    exposure_return=capital_context.get("exposure_return_pct")
    if traced_capital>0 and exposure_pnl>0.01:
        rationale += (
            f" Campaign accounting is {exposure_pnl:.2f} USD above its traced capital basis"
            + (f" ({_f(exposure_return):.1f}%)" if exposure_return is not None else "")
            + "; preserving or banking gains becomes more relevant if the fee/thesis case weakens, but profit alone does not force an exit."
        )
    elif traced_capital>0 and exposure_pnl<-0.01:
        rationale += (
            f" Campaign accounting is {abs(exposure_pnl):.2f} USD below its traced capital basis"
            + (f" ({abs(_f(exposure_return)):.1f}%)" if exposure_return is not None else "")
            + "; the manager must not take extra risk merely to recover prior losses."
        )

    options=[
        {
            "action":"KEEP","label":"Keep current LP","recommended":recommended=="KEEP",
            "status":"EARNING" if in_range else "WAITING_FOR_REENTRY",
            "expected_fees_usd":round(keep_fees,2),
            "expected_net_usd":round(keep_fees,2),
            "execution_cost_usd":0.0,
            "quality":current_projection.get("quality"),
            "summary":current_projection.get("note"),
        },
        {
            "action":"RE_RANGE","label":"Re-range","recommended":recommended=="RE_RANGE",
            "status":"AVAILABLE" if rerange_available else "UNAVAILABLE",
            "expected_fees_usd":round(rerange_fees,2) if rerange_fees is not None else None,
            "expected_net_usd":round(rerange_net,2) if rerange_net is not None else None,
            "execution_cost_usd":round(intervention,2) if intervention is not None else None,
            "break_even_days":round(break_even,2) if break_even is not None and break_even>=0 else None,
            "forecast_apr_pct":forecast.get("forecast_fee_apr_pct"),
            "confidence":confidence,
            "range":{
                "lower":best.get("lower"),"upper":best.get("upper"),
                "price_lens":best.get("price_lens") or (profit_result or {}).get("price_lens"),
            } if rerange_available else None,
            "summary":(
                "Close the current NFT, preserve the campaign accounting, then reopen at the Profit Lab range. The old leg keeps its realised result; the new leg becomes another position in the same campaign."
                if rerange_available else
                f"Fresh range economics are currently unavailable. {profit_error}".strip()
            ),
        },
        {
            "action":"HODL_WAIT","label":"HODL / wait","recommended":recommended=="HODL_WAIT",
            "status":"AVAILABLE",
            "expected_fees_usd":0.0,
            "expected_net_usd":None,
            "directional_return_usd":None,
            "execution_cost_usd":0.0,
            "summary":(
                "Do not force a redeployment. When already out of range, leaving the one-sided NFT in place can behave like holding while preserving automatic re-entry. "
                + (f"Fresh thesis is {thesis_flags.get('stance')} with {thesis_flags.get('hold_comfort')} holding comfort." if thesis_flags.get("fresh") else "Research thesis before treating this as a directional conviction.")
            ),
        },
        {
            "action":"EXIT","label":"Exit campaign","recommended":recommended=="EXIT",
            "status":("THESIS_CAUTION" if thesis_flags.get("strong_exit_caution") else "AVAILABLE" if thesis_flags.get("fresh") else "REQUIRES_THESIS"),
            "expected_fees_usd":0.0,
            "expected_net_usd":None,
            "directional_return_usd":None,
            "execution_cost_usd":None,
            "summary":(
                "Close the LP and swap out of the campaign asset. This is recommended only when fresh thesis evidence says the resulting token inventory is not acceptable to hold."
                if thesis_flags.get("strong_exit_caution")
                else "Close the LP and swap out of the campaign asset only when fresh thesis evidence outweighs the fee/holding case."
            ),
        },
    ]

    return {
        "campaign_id":campaign_id,
        "campaign_label":campaign.get("label"),
        "generated_for_position_id":active_id,
        "pair":position.get("pair"),
        "chain":position.get("chain"),
        "pool_address":position.get("pool_address"),
        "sleeve":sleeve,
        "horizon_days":round(horizon,3),
        "monthly_target_pct":round(max(0.0,_f(monthly_target_pct)),2),
        "rebalance_capital_usd":round(current_capital,2),
        "wallet_campaign_inventory_usd":round(_f(wallet.get("value_usd")),2),
        "wallet_inventory_auto_deployed":False,
        "current_position":{
            "id":active_id,
            "display_name":position.get("display_name") or position.get("pair"),
            "range_state":state,
            "risk_band":risk.get("band"),
            "risk_reason":risk.get("reason"),
            "nearest_edge_pct":None if nearest is None else round(nearest,3),
            "lower_price":position.get("lower_price"),
            "upper_price":position.get("upper_price"),
            "current_price":position.get("current_price"),
            "current_lp_value_usd":round(_f(acct.get("current_principal_usd")),2),
            "tracked_fees_usd":round(_f(acct.get("fees_earned_usd")),2),
            "net_pnl_after_costs_usd":acct.get("net_pnl_after_costs_usd"),
            "fee_projection":current_projection,
        },
        "recommended_action":recommended,
        "decision_confidence":confidence if rerange_available else "LIMITED",
        "thesis":thesis,
        "thesis_flags":thesis_flags,
        "headline":headline,
        "rationale":rationale,
        "options":options,
        "profit_lab_prefill":{
            "chain":position.get("chain"),
            "pool_address":position.get("pool_address"),
            "horizon_days":round(horizon,3),
            "sleeve":sleeve,
            "capital_usd":round(current_capital,2),
            "monthly_target_pct":round(max(0.0,_f(monthly_target_pct)),2),
            "source_campaign_id":campaign_id,
            "source_position_id":active_id,
        },
        "profit_result":profit_result,
        "campaign_summary":{
            "capital_invested_usd":capital_context.get("capital_invested_usd"),
            "marked_exposure_usd":capital_context.get("marked_exposure_usd",campaign.get("marked_exposure_usd")),
            "exposure_pnl_usd":capital_context.get("exposure_pnl_usd"),
            "exposure_return_pct":capital_context.get("exposure_return_pct"),
            "profit_cushion_usd":capital_context.get("profit_cushion_usd"),
            "capital_shortfall_usd":capital_context.get("capital_shortfall_usd"),
            "capital_state":capital_context.get("state"),
            "movement_count":capital_context.get("movement_count"),
            "known_campaign_pnl_usd":campaign.get("known_campaign_pnl_usd"),
            "realised_position_pnl_usd":campaign.get("realised_position_pnl_usd"),
            "open_position_pnl_usd":campaign.get("open_position_pnl_usd"),
            "lifetime_fees_usd":campaign.get("lifetime_fees_usd"),
            "transaction_costs_usd":campaign.get("transaction_costs_usd"),
            "wallet_inventory":wallet,
        },
        "limitations":[
            "v0.9.3 combines fee/redeployment economics with fresh campaign thesis evidence; sentiment never invents fee income.",
            "Directional return remains uncertain: bullish/bearish thesis changes management preference and range skew, not guaranteed P/L.",
            "Transaction-traced campaign capital and exposure P/L are context for capital preservation; they do not by themselves override fee/range/thesis evidence.",
            "Wallet campaign inventory is shown but is not automatically added to rebalance capital.",
            "Re-range remains an explicit close-then-open workflow requiring browser-wallet approval.",
        ],
    }
