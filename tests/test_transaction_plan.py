from lp_manager.transaction_plan import TransactionPlan, validate_transaction_plan


def test_open_plan_contract():
    p = TransactionPlan(action="OPEN", chain="BASE", protocol="UNISWAP_V3", pair="WETH/USDC", intent={"lower_price": 3000, "upper_price": 5000, "capital_value": 1000})
    v = validate_transaction_plan(p)
    assert v["valid"]
    assert "NO_CALLDATA_BUILT" in v["warnings"]


def test_position_action_requires_position_id():
    p = TransactionPlan(action="COLLECT", chain="BASE", protocol="UNISWAP_V3")
    assert not validate_transaction_plan(p)["valid"]
