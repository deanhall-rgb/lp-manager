from lp_manager.portfolio_advisor import rank_opportunities


def _row(pair,sleeve,score,net):
    return {"pair":pair,"chain":"BASE","pool_address":"0x"+pair.encode().hex()[:40].ljust(40,'0'),"sleeve":sleeve,
            "evaluation":{"core_pre_score":score,"tactical_pre_score":score,"risk_core":{"eligible":True,"blockers":[]},"risk_tactical":{"eligible":True,"blockers":[]}},
            "economics":{"capital_usd":1000,"estimated_net_month_pct":net,"estimated_net_month_usd":net*10,"mode":"ESTIMATED_FROM_VOLUME_TVL_RANGE","confidence":"LOW_TO_MODERATE"},
            "regime":{"confidence":70}}


def test_advisor_supports_core_tactical_and_best_only_filters():
    rows=[_row("WETH/USDC","CORE_INCOME",95,6),_row("DELTA/WETH","TACTICAL_CAMPAIGN",90,12)]
    core=rank_opportunities(rows,available_capital=1000,reserve_pct=10,sleeve_filter="CORE_INCOME")
    assert core["allocations"] and all(x["sleeve"]=="CORE_INCOME" for x in core["allocations"])
    tactical=rank_opportunities(rows,available_capital=1000,reserve_pct=10,sleeve_filter="TACTICAL_CAMPAIGN")
    assert tactical["allocations"] and all(x["sleeve"]=="TACTICAL_CAMPAIGN" for x in tactical["allocations"])
    best=rank_opportunities(rows,available_capital=1000,reserve_pct=10,allocation_mode="BEST_ONLY")
    assert len(best["allocations"])==1
    assert best["allocated"] <= 900
