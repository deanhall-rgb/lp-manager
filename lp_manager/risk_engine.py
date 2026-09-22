from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .portfolio_policy import CORE_INCOME, TACTICAL_CAMPAIGN


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


@dataclass(frozen=True)
class RiskAssessment:
    overall_risk: float
    risk_band: str
    sleeve: str
    eligible: bool
    blockers: list[str]
    components: dict[str, float]
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_pool_risk(candidate: dict[str, Any], *, sleeve: str) -> dict[str, Any]:
    """Explainable 0..100 risk score; higher is worse.

    The input is intentionally provider-neutral. Missing evidence is treated as
    uncertainty/risk rather than silently as safety.
    """
    sleeve = str(sleeve or TACTICAL_CAMPAIGN).upper()
    core = sleeve == CORE_INCOME
    blockers: list[str] = []
    evidence: list[str] = []

    chain_quality = _f(candidate.get("chain_quality"), 50.0)
    protocol_quality = _f(candidate.get("protocol_quality"), chain_quality)
    asset_conviction = _f(candidate.get("asset_conviction"), 50.0)
    token_quality = _f(candidate.get("token_quality"), 50.0)
    liquidity_stability = _f(candidate.get("liquidity_stability"), 50.0)
    fee_consistency = _f(candidate.get("fee_consistency"), 50.0)
    pool_age = _f(candidate.get("pool_age_days"), 0.0)
    tvl = max(0.0, _f(candidate.get("tvl_usd"), 0.0))
    volatility = max(0.0, _f(candidate.get("historical_volatility"), 50.0))
    concentration = _f(candidate.get("liquidity_concentration_risk"), 50.0)
    contract_risk = _f(candidate.get("contract_risk"), 25.0)
    stablecoin_risk = _f(candidate.get("stablecoin_risk"), 15.0)
    execution_drag = max(0.0, _f(candidate.get("gas_drag_pct"), 0.0))
    exit_liquidity = _f(candidate.get("exit_liquidity_score"), liquidity_stability)

    components = {
        "chain": _clamp(100.0 - chain_quality),
        "protocol": _clamp(100.0 - protocol_quality),
        "asset": _clamp(100.0 - (asset_conviction * 0.6 + token_quality * 0.4)),
        "liquidity": _clamp(100.0 - (liquidity_stability * 0.65 + exit_liquidity * 0.35)),
        "fees": _clamp(100.0 - fee_consistency),
        "volatility": _clamp(volatility),
        "concentration": _clamp(concentration),
        "contract": _clamp(contract_risk),
        "stablecoin": _clamp(stablecoin_risk),
        "execution": _clamp(execution_drag * 15.0),
        "history": _clamp(100.0 - min(100.0, pool_age / (90.0 if core else 14.0) * 100.0)),
    }

    weights = {
        "chain": 0.08,
        "protocol": 0.08,
        "asset": 0.17 if core else 0.14,
        "liquidity": 0.16,
        "fees": 0.08,
        "volatility": 0.10 if core else 0.12,
        "concentration": 0.07,
        "contract": 0.10,
        "stablecoin": 0.06,
        "execution": 0.04,
        "history": 0.06 if core else 0.07,
    }
    overall = sum(components[k] * weights[k] for k in weights)

    if core:
        if chain_quality < 80:
            blockers.append("CORE_CHAIN_QUALITY")
        if protocol_quality < 80:
            blockers.append("CORE_PROTOCOL_QUALITY")
        if asset_conviction < 80:
            blockers.append("CORE_ASSET_CONVICTION")
        if liquidity_stability < 75 or exit_liquidity < 75:
            blockers.append("CORE_LIQUIDITY_QUALITY")
        if pool_age < 30:
            blockers.append("CORE_HISTORY_TOO_SHORT")
        if tvl < 1_000_000:
            blockers.append("CORE_TVL_TOO_LOW")
        if contract_risk > 35:
            blockers.append("CORE_CONTRACT_RISK")
        max_risk = 38.0
    else:
        if chain_quality < 60:
            blockers.append("TACTICAL_CHAIN_QUALITY")
        if token_quality < 50:
            blockers.append("TACTICAL_TOKEN_QUALITY")
        if liquidity_stability < 40 or exit_liquidity < 40:
            blockers.append("TACTICAL_EXIT_LIQUIDITY")
        if pool_age < 2:
            blockers.append("TACTICAL_HISTORY_TOO_SHORT")
        if tvl < 150_000:
            blockers.append("TACTICAL_TVL_TOO_LOW")
        if contract_risk > 60:
            blockers.append("TACTICAL_CONTRACT_RISK")
        max_risk = 58.0

    if candidate.get("audited_contract") is True:
        evidence.append("AUDITED_CONTRACT")
        overall = max(0.0, overall - 3.0)
    if candidate.get("downside_inventory_desirable") is True and asset_conviction >= 80:
        evidence.append("DOWNSIDE_INVENTORY_ACCEPTABLE")
    if execution_drag > 2.0:
        evidence.append("EXECUTION_DRAG_MATERIAL")
    if concentration > 70:
        evidence.append("LIQUIDITY_CONCENTRATION_HIGH")

    band = "LOW" if overall < 25 else "MODERATE" if overall < 45 else "HIGH" if overall < 65 else "VERY_HIGH"
    eligible = not blockers and overall <= max_risk
    if not blockers and overall > max_risk:
        blockers.append("COMPOSITE_RISK_ABOVE_SLEEVE_LIMIT")
        eligible = False

    return RiskAssessment(
        overall_risk=round(_clamp(overall), 2),
        risk_band=band,
        sleeve=sleeve,
        eligible=eligible,
        blockers=sorted(set(blockers)),
        components={k: round(v, 2) for k, v in components.items()},
        evidence=evidence,
    ).to_dict()
