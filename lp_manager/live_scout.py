from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .risk_engine import assess_pool_risk
from .economics_engine import volume_quality
from .asset_registry import RISK_MAJOR_SYMBOLS, STABLE_SYMBOLS, CORE_MAJOR_SYMBOLS, MATURE_CORE_ASSET_SYMBOLS

# Compatibility aliases: one canonical registry now owns these classifications.
RISK_MAJORS = RISK_MAJOR_SYMBOLS
STABLES = STABLE_SYMBOLS
MAJORS = CORE_MAJOR_SYMBOLS


def _age_days(value: Any) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:
        return None


def preliminary_pool_evaluation(pool: dict[str, Any]) -> dict[str, Any]:
    b = str((pool.get("base_token") or {}).get("symbol") or "").upper()
    q = str((pool.get("quote_token") or {}).get("symbol") or "").upper()
    symbols = {b, q}
    tvl = float(pool.get("tvl_usd") or 0)
    vol = float(pool.get("volume_24h_usd") or 0)
    age_value = _age_days(pool.get("pool_created_at"))
    age_known = age_value is not None
    age = float(age_value or 0.0)
    major_count = len(symbols & MAJORS)
    has_stable = bool(symbols & STABLES)
    core_pair = bool(symbols & RISK_MAJORS) and bool(symbols & STABLES)
    stable_pair = len(symbols) >= 2 and symbols.issubset(STABLES)
    mature_structured_pair = len(symbols) >= 2 and symbols.issubset(MATURE_CORE_ASSET_SYMBOLS)
    asset_quality = 98.0 if major_count == 2 else 82.0 if major_count == 1 else 48.0
    liquidity_score = min(100.0, 35.0 + 13.0 * max(0.0, __import__('math').log10(max(1.0, tvl / 10000))))
    activity = min(100.0, 30.0 + 18.0 * max(0.0, __import__('math').log10(max(1.0, vol / 10000))))
    history_quality = min(100.0, age / 3) if age_known else 50.0
    core_pre = 0.34 * asset_quality + 0.31 * liquidity_score + 0.20 * activity + 0.15 * history_quality
    tactical_pre = 0.28 * asset_quality + 0.25 * liquidity_score + 0.40 * activity + 7.0
    activity_quality = volume_quality(pool)
    severe_activity_anomaly = activity_quality["factor"] < 0.20
    candidate = {
        **pool,
        "pool_age_days": age,
        "pool_age_known": age_known,
        "asset_conviction": asset_quality,
        "token_quality": asset_quality,
        "chain_quality": 90.0,
        "protocol_quality": 92.0 if str(pool.get("protocol")).upper() == "UNISWAP_V3" else 70.0,
        "liquidity_stability": round(liquidity_score, 1),
        "fee_consistency": 55.0,
        "historical_volatility": 0.0,
        "gas_drag_pct": 0.0,
        "stablecoin_risk": 12.0 if has_stable else 25.0,
        "contract_risk": 12.0 if str(pool.get("protocol")).upper() == "UNISWAP_V3" else 35.0,
        "exit_liquidity_score": liquidity_score,
        "audited_contract": True if str(pool.get("protocol")).upper() == "UNISWAP_V3" else None,
        "activity_quality_factor": activity_quality["factor"],
        "activity_quality_flags": activity_quality["flags"],
    }
    risk_core = assess_pool_risk(candidate, sleeve="CORE_INCOME")
    risk_tactical = assess_pool_risk(candidate, sleeve="TACTICAL_CAMPAIGN")

    preferred = None
    sleeve_reason = "UNCLASSIFIED"
    pair_policy_core = bool(core_pair or stable_pair or mature_structured_pair)

    # Asset maturity defines the *default* inventory philosophy, not an absolute
    # ban on Tactical use. If a mature pair fails Core durability/liquidity but
    # genuinely clears Tactical risk, it can be treated as a campaign rather
    # than disappearing from the opportunity set.
    if pair_policy_core and risk_core.get("eligible"):
        preferred = "CORE_INCOME"
        sleeve_reason = "MATURE_PAIR_CLEARS_CORE"
    elif pair_policy_core and not severe_activity_anomaly and risk_tactical.get("eligible"):
        preferred = "TACTICAL_CAMPAIGN"
        sleeve_reason = "MATURE_PAIR_TACTICAL_OVERRIDE"
    elif (not pair_policy_core) and not severe_activity_anomaly and risk_tactical.get("eligible"):
        preferred = "TACTICAL_CAMPAIGN"
        sleeve_reason = "CAMPAIGN_PAIR_CLEARS_TACTICAL"
    elif (
        (not pair_policy_core)
        and not severe_activity_anomaly
        and risk_core.get("eligible")
        and core_pre >= 72
        and asset_quality >= 90
        and age >= 90
        and tvl >= 1_000_000
    ):
        preferred = "CORE_INCOME"
        sleeve_reason = "NON_POLICY_PAIR_EARNS_CORE"

    return {
        "preliminary": True,
        "core_pre_score": round(core_pre, 1),
        "tactical_pre_score": round(tactical_pre, 1),
        "preferred_sleeve": preferred,
        "pair_policy_sleeve": "CORE_INCOME" if pair_policy_core else "TACTICAL_CAMPAIGN",
        "sleeve_reason": sleeve_reason,
        "quality": {"asset": round(asset_quality,1), "liquidity": round(liquidity_score,1), "activity": round(activity,1), "age_days": round(age,1) if age_known else None, "age_known": age_known, "activity_persistence": round(activity_quality["factor"]*100,1)},
        "quality_flags": activity_quality["flags"],
        "risk_core": risk_core,
        "risk_tactical": risk_tactical,
        "note": "Pair policy sets the default inventory philosophy. Mature pairs that clear Core remain Core; if Core fails but the pool clears Tactical risk, the same pair may be treated as a Tactical fee campaign. Non-mature pairs default Tactical when they clear Tactical risk.",
    }
