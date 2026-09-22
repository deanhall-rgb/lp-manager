from lp_manager.risk_engine import assess_pool_risk


def mature_core():
    return {
        "chain_quality": 95, "protocol_quality": 95, "asset_conviction": 95, "token_quality": 95,
        "liquidity_stability": 92, "fee_consistency": 82, "pool_age_days": 700, "tvl_usd": 50_000_000,
        "historical_volatility": 35, "liquidity_concentration_risk": 20, "contract_risk": 10,
        "stablecoin_risk": 12, "gas_drag_pct": 0.2, "exit_liquidity_score": 95, "audited_contract": True,
    }


def test_mature_pool_can_pass_core_risk():
    r = assess_pool_risk(mature_core(), sleeve="CORE_INCOME")
    assert r["eligible"]
    assert r["risk_band"] in {"LOW", "MODERATE"}


def test_fragile_pool_fails_core():
    c = mature_core(); c.update({"liquidity_stability": 30, "tvl_usd": 200_000, "pool_age_days": 5})
    r = assess_pool_risk(c, sleeve="CORE_INCOME")
    assert not r["eligible"]
    assert "CORE_LIQUIDITY_QUALITY" in r["blockers"]
