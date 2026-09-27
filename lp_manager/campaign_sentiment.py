from __future__ import annotations

import math
import time
from typing import Any


STANCE_VALUE={
    "BULLISH":1.0,
    "LEAN_BULLISH":0.5,
    "NEUTRAL":0.0,
    "LEAN_BEARISH":-0.5,
    "BEARISH":-1.0,
    "UNKNOWN":0.0,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def thesis_is_fresh(thesis: dict[str,Any] | None, *, max_age_hours: float = 6.0) -> bool:
    if not thesis:
        return False
    generated=_f(thesis.get("generated_at"))
    if generated<=0:
        return False
    return time.time()-generated <= max(0.25,max_age_hours)*3600.0


def _parse_unit(unit: str) -> tuple[str,str]:
    u=str(unit or "").upper()
    if "_PER_" not in u:
        return "",""
    num,den=u.split("_PER_",1)
    return num.strip(),den.strip()


def asset_direction_from_ratio(direction: str, unit: str, asset_symbol: str) -> str:
    """Translate ratio momentum into the campaign asset's direction.

    Example: DELTA_PER_WETH falling means DELTA strengthens versus WETH, so a
    BEARISH ratio is BULLISH DELTA. If the campaign asset is the denominator,
    ratio direction and asset direction are aligned.
    """
    d=str(direction or "NEUTRAL").upper()
    if d not in {"BULLISH","BEARISH","NEUTRAL"}:
        d="NEUTRAL"
    numerator,denominator=_parse_unit(unit)
    asset=str(asset_symbol or "").upper()
    if asset and asset==numerator:
        return {"BULLISH":"BEARISH","BEARISH":"BULLISH","NEUTRAL":"NEUTRAL"}[d]
    if asset and asset==denominator:
        return d
    return "NEUTRAL"


def technical_fallback(asset_symbol: str, regime: dict[str,Any], price_lens: dict[str,Any]) -> dict[str,Any]:
    ratio_direction=str(regime.get("direction") or "NEUTRAL").upper()
    unit=str(price_lens.get("unit") or "")
    asset_direction=asset_direction_from_ratio(ratio_direction,unit,asset_symbol)
    if asset_direction=="BULLISH":
        stance="LEAN_BULLISH"
    elif asset_direction=="BEARISH":
        stance="LEAN_BEARISH"
    else:
        stance="NEUTRAL"
    return {
        "stance":stance,
        "confidence":round(min(65.0,max(15.0,_f(regime.get("confidence"),25.0))),1),
        "hold_comfort":"UNKNOWN",
        "hold_comfort_score":50.0,
        "technical_signal":asset_direction,
        "technical_ratio_direction":ratio_direction,
        "technical_unit":unit,
        "research_summary":"Technical price-path evidence only; current asset/news research has not yet been completed.",
        "hold_reason":"Holding comfort cannot be established from price momentum alone.",
        "range_implication":"Use technical regime as a bounded range-geometry input; do not infer fundamental conviction.",
        "catalysts":[],
        "risks":["No independent asset/news research is present."],
        "invalidation":"A material regime change or new asset-specific information.",
        "sources":[],
        "research_quality":"TECHNICAL_ONLY",
    }


def normalise_thesis(
    row: dict[str,Any],
    *,
    campaign_id: str,
    asset_symbol: str,
    asset_address: str,
    chain: str,
    technical: dict[str,Any],
) -> dict[str,Any]:
    stance=str(row.get("stance") or technical.get("stance") or "UNKNOWN").upper()
    if stance not in STANCE_VALUE:
        stance="UNKNOWN"
    hold=str(row.get("hold_comfort") or "UNKNOWN").upper()
    if hold not in {"COMFORTABLE","CONDITIONAL","UNCOMFORTABLE","UNKNOWN"}:
        hold="UNKNOWN"
    confidence=max(0.0,min(100.0,_f(row.get("confidence"),_f(technical.get("confidence"),25.0))))
    hold_score=max(0.0,min(100.0,_f(row.get("hold_comfort_score"),50.0)))
    now=time.time()
    return {
        **row,
        "campaign_id":campaign_id,
        "asset_symbol":str(asset_symbol or "").upper(),
        "asset_address":asset_address or "",
        "chain":str(chain or "").upper(),
        "stance":stance,
        "confidence":round(confidence,1),
        "hold_comfort":hold,
        "hold_comfort_score":round(hold_score,1),
        "technical":technical,
        "generated_at":now,
        "fresh_until":now+6*3600,
        "research_quality":str(row.get("research_quality") or ("WEB_RESEARCH" if row.get("sources") else technical.get("research_quality") or "TECHNICAL_ONLY")),
    }


def execution_skew_pct(
    thesis: dict[str,Any] | None,
    *,
    unit: str,
    asset_symbol: str,
    sleeve: str = "TACTICAL_CAMPAIGN",
) -> float:
    """Convert asset stance into a bounded execution-price skew.

    Positive skew means more room ABOVE the execution ratio. If the campaign
    asset is the numerator (DELTA_PER_WETH), bullish asset conviction means the
    ratio is expected to fall, so the range is nudged DOWN instead.
    """
    if not thesis or not thesis_is_fresh(thesis):
        return 0.0
    stance=str(thesis.get("stance") or "UNKNOWN").upper()
    directional=STANCE_VALUE.get(stance,0.0)
    confidence=max(0.0,min(1.0,_f(thesis.get("confidence"))/100.0))
    numerator,denominator=_parse_unit(unit)
    asset=str(asset_symbol or "").upper()
    if asset==numerator:
        directional*=-1.0
    elif asset!=denominator:
        return 0.0
    max_abs=5.0 if str(sleeve or "").upper()=="CORE_INCOME" else 3.0
    return round(max(-max_abs,min(max_abs,directional*confidence*max_abs)),2)


def thesis_decision_flags(thesis: dict[str,Any] | None) -> dict[str,Any]:
    fresh=thesis_is_fresh(thesis)
    stance=str((thesis or {}).get("stance") or "UNKNOWN").upper()
    hold=str((thesis or {}).get("hold_comfort") or "UNKNOWN").upper()
    confidence=_f((thesis or {}).get("confidence"))
    return {
        "fresh":fresh,
        "stance":stance,
        "hold_comfort":hold,
        "confidence":round(confidence,1),
        "strong_exit_caution":bool(fresh and hold=="UNCOMFORTABLE" and confidence>=65),
        "positive_hold_thesis":bool(fresh and hold in {"COMFORTABLE","CONDITIONAL"} and stance in {"BULLISH","LEAN_BULLISH"} and confidence>=50),
        "negative_thesis":bool(fresh and stance in {"BEARISH","LEAN_BEARISH"} and confidence>=60),
    }
