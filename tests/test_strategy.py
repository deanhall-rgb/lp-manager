from lp_manager.strategy import edge_risk, deterministic_plan


def tactical_pos(current=15.8):
    return {
        "id": "x", "pair": "DELTA/WETH", "lower_price": 14.75, "upper_price": 21.5,
        "current_price": current, "apr_current": 70, "strategy_sleeve": "TACTICAL_CAMPAIGN",
        "inventory_intent": "ACCUMULATE_RISK_ASSET",
    }


def core_pos(current=15.0):
    return {
        "id": "core", "pair": "WETH/USDC", "lower_price": 10.0, "upper_price": 20.0,
        "current_price": current, "apr_current": 80, "strategy_sleeve": "CORE_INCOME",
        "inventory_intent": "ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE",
    }


def test_comfortable_tactical_position_holds():
    risk = edge_risk(tactical_pos(18.0))
    assert risk["band"] in {"COMFORTABLE", "WATCH"}
    decision = deterministic_plan(tactical_pos(18.0))
    assert decision.action in {"CAMPAIGN_HOLD", "CAMPAIGN_HOLD_WATCH"}


def test_tactical_out_above_prepares_campaign_move():
    decision = deterministic_plan(tactical_pos(22.0))
    assert decision.action == "CAMPAIGN_REVIEW_UPWARD_RECENTER_OR_BANK"
    assert decision.severity == "ACTION"


def test_core_out_below_can_be_intentional_inventory():
    decision = deterministic_plan(core_pos(9.0))
    assert decision.action == "CORE_HOLD_INVENTORY_OR_RECENTER"
    assert decision.severity == "WATCH"


def test_core_is_less_twitchy_than_tactical():
    core = edge_risk({**core_pos(), "current_price": 10.8})
    tactical = edge_risk({**tactical_pos(), "lower_price": 10.0, "upper_price": 20.0, "current_price": 10.8})
    assert core["score"] <= tactical["score"]
