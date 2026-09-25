from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .risk_engine import assess_pool_risk
from .economics_engine import volume_quality

RISK_MAJORS = {"WETH","ETH","WBTC","BTC"}
STABLES = {"USDC","USDT","USDG","DAI","USDS","USDBC","FRAX","GHO","LUSD"}
MAJORS = RISK_MAJORS | STABLES


def _age_days(value: Any) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:
        return 0.0


def preliminary_pool_evaluation(pool: dict[str, Any]) -> dict[str, Any]:
    b = str((pool.get("base_token") or {}).get("symbol") or "").upper()
    q = str((pool.get("quote_token") or {}).get("symbol") or "").upper()
    symbols = {b, q}
    tvl = float(pool.get("tvl_usd") or 0)
    vol = float(pool.get("volume_24h_usd") or 0)
    age = _age_days(pool.get("pool_created_at"))
    major_count = len(symbols & MAJORS)
    has_stable = bool(symbols & STABLES)
    core_pair = bool(symbols & RISK_MAJORS) and bool(symbols & STABLES)
    stable_pair = len(symbols) >= 2 and symbols.issubset(STABLES)
    asset_quality = 98.0 if major_count == 2 else 82.0 if major_count == 1 else 48.0
    liquidity_score = min(100.0, 35.0 + 13.0 * max(0.0, __import__('math').log10(max(1.0, tvl / 10000))))
    activity = min(100.0, 30.0 + 18.0 * max(0.0, __import__('math').log10(max(1.0, vol / 10000))))
    core_pre = 0.34 * asset_quality + 0.31 * liquidity_score + 0.20 * activity + 0.15 * min(100.0, age / 3)
    tactical_pre = 0.28 * asset_quality + 0.25 * liquidity_score + 0.40 * activity + 7.0
    activity_quality = volume_quality(pool)
    severe_activity_anomaly = activity_quality["factor"] < 0.20
    candidate = {
        **pool,
        "pool_age_days": age,
        "asset_conviction": asset_quality,
        "token_quality": asset_quality,
        "chain_quality": 90.0,
        "protocol_quality": 92.0 if str(pool.get("protocol")).upper() == "UNISWAP_V3" else 70.0,
        "liquidity_stability": 60.0,
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
    preferred = None
    # Sleeve is first an inventory/risk philosophy, then a quality gate. A WETH/
    # stable pool does not become a Tactical campaign merely because it is young.
    # Liquidity/risk gates can still reject the pool later.
    if core_pair or stable_pair:
        preferred = "CORE_INCOME"
    elif not severe_activity_anomaly and core_pre >= 72 and asset_quality >= 90 and age >= 90 and tvl >= 1_000_000:
        preferred = "CORE_INCOME"
    elif not severe_activity_anomaly and tactical_pre >= 68 and tvl >= 50_000:
        preferred = "TACTICAL_CAMPAIGN"
    return {
        "preliminary": True,
        "core_pre_score": round(core_pre, 1),
        "tactical_pre_score": round(tactical_pre, 1),
        "preferred_sleeve": preferred,
        "pair_policy_sleeve": "CORE_INCOME" if (core_pair or stable_pair) else "TACTICAL_CAMPAIGN",
        "quality": {"asset": round(asset_quality,1), "liquidity": round(liquidity_score,1), "activity": round(activity,1), "age_days": round(age,1), "activity_persistence": round(activity_quality["factor"]*100,1)},
        "quality_flags": activity_quality["flags"],
        "risk_core": assess_pool_risk(candidate, sleeve="CORE_INCOME"),
        "risk_tactical": assess_pool_risk(candidate, sleeve="TACTICAL_CAMPAIGN"),
        "note": "Pair policy classifies major/stable and stable/stable inventory as Core. Pre-score then measures whether the specific pool is attractive; risk gates may still reject it. Historical fee stability/range durability is added by Profit Lab.",
    }
