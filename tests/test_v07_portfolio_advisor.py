from lp_manager.portfolio_advisor import rank_opportunities


def row(pair, sleeve, score, net, chain="BASE"):
    return {"pair":pair,"chain":chain,"pool_address":"0x"+pair.encode().hex()[:40].ljust(40,'0'),"sleeve":sleeve,
            "evaluation":{"core_pre_score":score,"tactical_pre_score":score,"risk_core":{"eligible":True,"blockers":[]},"risk_tactical":{"eligible":True,"blockers":[]}},
            "economics":{"capital_usd":1000,"estimated_net_month_pct":net,"estimated_net_month_usd":net*10,"mode":"ESTIMATED_FROM_VOLUME_TVL_RANGE"},
            "regime":{"confidence":70}}


def test_advisor_can_concentrate_capital_in_best_opportunity_but_keeps_reserve():
    r=rank_opportunities([row("WETH/USDC","CORE_INCOME",94,8),row("ETH/USDT","CORE_INCOME",70,2),row("DELTA/WETH","TACTICAL_CAMPAIGN",80,12)],available_capital=1000,reserve_pct=10,max_positions=3)
    assert r["reserve"] >= 100
    assert r["allocations"]
    assert r["allocations"][0]["pair"] == "WETH/USDC"
    tactical=sum(x["amount"] for x in r["allocations"] if x["sleeve"]=="TACTICAL_CAMPAIGN")
    assert tactical <= 270.01
