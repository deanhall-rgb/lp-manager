import time
from types import SimpleNamespace
from pathlib import Path

from lp_manager.candidate_universe import CandidateUniverse
from lp_manager.opportunity_leaderboard import OpportunityLeaderboard


def pool(address, pair, tvl, volume, score_fee=0):
    a,b=pair.split("/")
    return {
        "chain":"ETHEREUM",
        "pool_address":address,
        "pair":pair,
        "base_token":{"symbol":a,"address":"0x"+"1"*40},
        "quote_token":{"symbol":b,"address":"0x"+"2"*40},
        "tvl_usd":tvl,
        "volume_24h_usd":volume,
        "fees_24h_usd":score_fee,
        "version":"V3",
        "protocol_guess":"UNISWAP_V3",
        "source":"THEGRAPH",
    }


def dex_pair(address,pair,tvl,volume):
    a,b=pair.split("/")
    return {
        "chainId":"ethereum",
        "pairAddress":address,
        "dexId":"uniswap",
        "labels":["v3"],
        "baseToken":{"address":"0x"+"3"*40,"symbol":a,"name":a},
        "quoteToken":{"address":"0x"+"4"*40,"symbol":b,"name":b},
        "liquidity":{"usd":tvl},
        "volume":{"h24":volume},
    }


class Store:
    def __init__(self):
        self.settings={}

    def set_setting(self,key,value):
        self.settings[key]=value

    def get_setting(self,key,default=None):
        return self.settings.get(key,default)

    def list_positions(self,status=None):
        return []

    def list_forecast_snapshots(self,limit=500):
        return []

    def list_opportunities(self,limit=1000):
        return []


class Discovery:
    def __init__(self):
        self.graph=[]
        self.dex=[]
        self.exact={}
        self.exact_calls=[]

    def graph_sample(self,chain,limit):
        return {
            "provider":"THEGRAPH","status":"OK" if self.graph else "EMPTY",
            "requests":1,"candidate_count":len(self.graph),"candidates":list(self.graph),
            "elapsed_ms":1,
        }

    def dexscreener_sample(self,chain):
        return {
            "provider":"DEXSCREENER","status":"OK" if self.dex else "EMPTY",
            "requests":1,"candidate_count":len(self.dex),"candidates":list(self.dex),
            "elapsed_ms":1,
        }

    def gecko_sample(self,chain,pages):
        return {
            "provider":"GECKOTERMINAL","status":"EMPTY","requests":1,
            "candidate_count":0,"candidates":[],"elapsed_ms":1,
        }

    def _dex_pair_lookup(self,chain,address):
        self.exact_calls.append(address.lower())
        return self.exact.get(address.lower()),None


def test_provider_sample_miss_does_not_delete_high_value_candidates_and_exact_lookup_revalidates():
    nvda="0x"+"a"*40
    spcx="0x"+"b"*40
    other="0x"+"c"*40
    store=Store()
    discovery=Discovery()
    discovery.graph=[
        pool(nvda,"NVDAON/USDC",325_000,1_050_000,900),
        pool(spcx,"SPCXON/USDC",300_000,650_000,700),
    ]
    universe=CandidateUniverse(SimpleNamespace(),store,discovery)

    first=universe.refresh("ETHEREUM",shortlist_limit=40,validate_limit=20,include_gecko=False)
    assert {x["pair"] for x in first["shortlist"]}=={"NVDAON/USDC","SPCXON/USDC"}

    # Next broad provider sample omits both previously valuable pools. Exact
    # DEX lookup can still see them, so persistence should keep and revalidate.
    discovery.graph=[pool(other,"OTHER/USDC",500_000,200_000,100)]
    discovery.exact={
        nvda.lower():dex_pair(nvda,"NVDAON/USDC",324_000,1_060_000),
        spcx.lower():dex_pair(spcx,"SPCXON/USDC",301_000,640_000),
        other.lower():dex_pair(other,"OTHER/USDC",500_000,200_000),
    }
    second=universe.refresh("ETHEREUM",shortlist_limit=40,validate_limit=20,include_gecko=False)

    pairs={x["pair"]:x for x in second["shortlist"]}
    assert "NVDAON/USDC" in pairs
    assert "SPCXON/USDC" in pairs
    assert pairs["NVDAON/USDC"]["universe_retained"] is False
    assert pairs["SPCXON/USDC"]["universe_retained"] is False
    assert pairs["NVDAON/USDC"]["universe_origin"]=="TARGETED_REVALIDATION"
    assert pairs["SPCXON/USDC"]["universe_origin"]=="TARGETED_REVALIDATION"
    assert second["summary"]["retained_revalidated"]>=2


def test_unrevalidated_candidate_stays_visible_but_cannot_confirm_allocation():
    nvda="0x"+"d"*40
    other="0x"+"e"*40
    store=Store()
    discovery=Discovery()
    discovery.graph=[pool(nvda,"NVDAON/USDC",325_000,1_050_000,900)]
    universe=CandidateUniverse(SimpleNamespace(),store,discovery)
    first=universe.refresh("ETHEREUM",shortlist_limit=40,validate_limit=20,include_gecko=False)
    assert first["shortlist"][0]["research_ready"] is False or first["shortlist"][0]["universe_retained"] is False

    # Make the first snapshot live-valid so it is a real persistent board row.
    saved=store.settings["candidate_universe:ETHEREUM"]
    saved_row=saved["shortlist"][0]
    saved_row["economic_validation"]="CROSS_VALIDATED"
    saved_row["research_ready"]=True
    saved_row["profit_lab_readiness"]={"ready":True,"status":"READY","label":"PROFIT READY"}

    discovery.graph=[pool(other,"OTHER/USDC",600_000,300_000,100)]
    discovery.exact={other.lower():dex_pair(other,"OTHER/USDC",600_000,300_000)}
    second=universe.refresh("ETHEREUM",shortlist_limit=40,validate_limit=0,include_gecko=False)
    retained=next(x for x in second["shortlist"] if x["pair"]=="NVDAON/USDC")
    assert retained["universe_retained"] is True
    assert retained["universe_missed_refreshes"]==1

    board=OpportunityLeaderboard(store,universe).rebuild(chains=["ETHEREUM"],limit=25)
    row=next(x for x in board["rows"] if x["pair"]=="NVDAON/USDC")
    assert row["universe_retained"] is True
    assert row["market_fresh_for_allocation"] is False
    assert row["allocation_confirmed"] is False
    assert board["funnel"]["retained_candidates"]>=1


def test_retention_expires_after_guardrail_window():
    store=Store()
    universe=CandidateUniverse(SimpleNamespace(),store,Discovery())
    old="0x"+"f"*40
    prior={
        "generated_at":time.time()-7200,
        "shortlist":[{
            **pool(old,"OLD/USDC",200_000,300_000,100),
            "universe_last_seen_at":time.time()-7200,
            "universe_missed_refreshes":6,
            "economic_validation":"CROSS_VALIDATED",
        }],
    }
    merged,stats=universe._merge_persistent_candidates([],prior,now=time.time())
    assert merged==[]
    assert stats["expired_retained"]==1


def test_v0976_ui_exposes_retained_revalidation_state():
    root=Path(__file__).resolve().parents[1]
    app=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")
    assert "RETAINED ${ageText}" in app
    assert "revalidation pending" in app
    assert "Retained / recheck" in app
