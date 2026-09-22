from __future__ import annotations

import time
import uuid
from typing import Any

from .analytics import range_metrics
from .models import Decision
from .portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN, policy_for


def edge_risk(position: dict[str, Any]) -> dict[str, Any]:
    r = range_metrics(position)
    state = r["range_state"]
    nearest = r.get("nearest_edge_pct")
    sleeve = str(position.get("strategy_sleeve") or TACTICAL_CAMPAIGN).upper()
    policy = policy_for(sleeve)

    if state.startswith("OUT_"):
        score = 92.0 if sleeve == TACTICAL_CAMPAIGN else 78.0
        band = "INTERVENE" if sleeve == TACTICAL_CAMPAIGN else "WATCH"
        reason = "Position is out of range and not earning concentrated LP fees."
    elif nearest is None:
        score = 50.0
        band = "WATCH"
        reason = "Insufficient range data."
    elif nearest <= policy.intervene_edge_pct:
        score = 88.0
        band = "INTERVENE"
        reason = f"Nearest range edge is only {nearest:.1f}% away."
    elif nearest <= policy.watch_edge_pct:
        score = 70.0 if sleeve == TACTICAL_CAMPAIGN else 62.0
        band = "WATCH"
        reason = f"Nearest range edge is {nearest:.1f}% away."
    elif nearest <= policy.watch_edge_pct * 2:
        score = 45.0 if sleeve == TACTICAL_CAMPAIGN else 35.0
        band = "WATCH" if sleeve == TACTICAL_CAMPAIGN else "COMFORTABLE"
        reason = f"Range has useful room, but edge proximity is increasing ({nearest:.1f}%)."
    else:
        score = 20.0 if sleeve == TACTICAL_CAMPAIGN else 12.0
        band = "COMFORTABLE"
        reason = "Position has useful room to both range edges."

    # Tactical campaigns are deliberately more sensitive to decaying fee productivity.
    apr = float(position.get("apr_current") or 0.0)
    if sleeve == TACTICAL_CAMPAIGN and apr < 20 and score < 80:
        score = min(100.0, score + 12.0)
        reason += " Tactical fee productivity is also weak."

    return {
        "score": score,
        "band": band,
        "reason": reason,
        "strategy_sleeve": sleeve,
        "review_seconds": policy.strategy_review_seconds,
        "ai_review_seconds": policy.ai_review_seconds,
        **r,
    }


def deterministic_plan(position: dict[str, Any]) -> Decision:
    risk = edge_risk(position)
    pair = position.get("pair") or position.get("id")
    state = risk["range_state"]
    sleeve = risk["strategy_sleeve"]
    inventory_intent = str(position.get("inventory_intent") or "BALANCED").upper()

    if sleeve == CORE_INCOME:
        if state == "OUT_ABOVE":
            action = "CORE_REVIEW_UPPER_RANGE_OR_BANK_STABLE"
            severity = "WATCH"
            confidence = 0.84
            summary = f"{pair}: core position is above range; review whether to reopen higher or retain the stable-side inventory."
            rationale = "Core positions optimise durable net income, not constant recentering. Re-open only if the long-horizon thesis and expected fee recovery justify lifecycle costs."
        elif state == "OUT_BELOW":
            desirable = inventory_intent in {"ACCUMULATE_RISK_ASSET", "ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE"}
            action = "CORE_HOLD_INVENTORY_OR_RECENTER" if desirable else "CORE_REVIEW_DOWNSIDE_EXPOSURE"
            severity = "WATCH" if desirable else "ACTION"
            confidence = 0.86
            summary = f"{pair}: core position is below range; {'holding the risk asset is consistent with the plan' if desirable else 'review the downside inventory before doing anything'}."
            rationale = "A core LP can intentionally behave like a range order. Being out of range is not itself a failure when the resulting inventory is an asset we are happy to hold."
        elif risk["band"] == "INTERVENE":
            action = "CORE_PREPARE_CONTINGENCY"
            severity = "WATCH"
            confidence = 0.82
            summary = f"{pair}: core range is nearing an edge; prepare alternatives without forcing a rebalance."
            rationale = risk["reason"] + " Compare hold, extension and single-sided satellite ranges before paying lifecycle costs."
        else:
            action = "CORE_HOLD_AND_COMPOUND"
            severity = "INFO"
            confidence = 0.88
            summary = f"{pair}: maintain the core income range."
            rationale = risk["reason"] + " No material intervention is justified; allow fees and time-in-range to do the work."
    else:
        if state == "OUT_ABOVE":
            action = "CAMPAIGN_REVIEW_UPWARD_RECENTER_OR_BANK"
            severity = "ACTION"
            confidence = 0.88
            summary = f"{pair}: tactical campaign is above range; decide quickly between banking profit and moving the campaign upward."
            rationale = "Tactical capital should not remain idle. Re-test sentiment, momentum and post-cost fee opportunity before redeploying."
        elif state == "OUT_BELOW":
            action = "CAMPAIGN_REVIEW_EXIT_OR_RECOVERY"
            severity = "ACTION"
            confidence = 0.88
            summary = f"{pair}: tactical campaign is below range; revalidate the bullish/recovery thesis before holding the volatile inventory."
            rationale = "Campaign positions have a profit objective and shorter clock. A bullish thesis can justify waiting, but only while evidence and recovery economics remain intact."
        elif risk["band"] == "INTERVENE":
            action = "CAMPAIGN_PREPARE_NEXT_MOVE"
            severity = "ACTION"
            confidence = 0.84
            summary = f"{pair}: tactical edge risk is high; prepare the next range or exit now."
            rationale = risk["reason"] + " Tactical positions are monitored actively because capital turnover is part of the strategy."
        elif risk["band"] == "WATCH":
            action = "CAMPAIGN_HOLD_WATCH"
            severity = "WATCH"
            confidence = 0.78
            summary = f"{pair}: hold the campaign for now, with active monitoring."
            rationale = risk["reason"] + " Continue only while fee production and directional evidence justify the risk."
        else:
            action = "CAMPAIGN_HOLD"
            severity = "INFO"
            confidence = 0.84
            summary = f"{pair}: tactical campaign remains healthy."
            rationale = risk["reason"] + " There is no deterministic reason to move the campaign yet."

    return Decision(
        id=uuid.uuid4().hex,
        position_id=str(position.get("id")),
        created_at=time.time(),
        severity=severity,
        action=action,
        confidence=confidence,
        summary=summary,
        rationale=rationale,
        trigger=f"{sleeve}:EDGE_RISK:{risk['score']:.0f}",
    )
