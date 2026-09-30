from pathlib import Path
from types import SimpleNamespace

from lp_manager.candidate_universe import CandidateUniverse
from lp_manager.discovery_lab import DiscoveryLab


def pool(address, pair, tvl, volume, *, source="THEGRAPH", version="V3", fees=0):
    a,b=pair.split("/")
    return {
        "chain":"ETHEREUM",
        "pool_address":address,
        "pair":pair,
        "base_token":{"symbol":a,"address":"0x"+"1"*40},
        "quote_token":{"symbol":b,"address":"0x"+"2"*40},
        "tvl_usd":tvl,
        "volume_24h_usd":volume,
        "fees_24h_usd":fees,
        "version":version,
        "protocol_guess":"UNISWAP_V3" if version=="V3" else "UNISWAP_"+version,
        "source":source,
    }


class FakeStore:
    def __init__(self):
        self.settings={}
        self.positions=[]

    def set_setting(self,key,value):
        self.settings[key]=value

    def get_setting(self,key,default=None):
        return self.settings.get(key,default)

    def list_positions(self,status=None):
        rows=list(self.positions)
        if status:
            rows=[x for x in rows if x.get("status")==status]
        return rows


class FakeDiscovery:
    def __init__(self, graph=None, dex=None, gecko=None, exact=None):
        self.graph=graph or []
        self.dex=dex or []
        self.gecko=gecko or []
        self.exact=exact or {}
        self.exact_calls=[]

    def graph_sample(self,chain,limit):
        return {"provider":"THEGRAPH","status":"OK" if self.graph else "NO_SUBGRAPH","requests":1 if self.graph else 0,
                "candidate_count":len(self.graph),"candidates":self.graph,"elapsed_ms":5,"subgraph_id":"graph-test" if self.graph else None}

    def dexscreener_sample(self,chain):
        return {"provider":"DEXSCREENER","status":"OK" if self.dex else "EMPTY","requests":1,
                "candidate_count":len(self.dex),"candidates":self.dex,"elapsed_ms":2}

    def gecko_sample(self,chain,pages):
        return {"provider":"GECKOTERMINAL","status":"OK" if self.gecko else "EMPTY","requests":1,
                "candidate_count":len(self.gecko),"candidates":self.gecko,"elapsed_ms":3}

    def _dex_pair_lookup(self,chain,address):
        self.exact_calls.append(address.lower())
        return self.exact.get(address.lower()),None


def dex_pair(address,pair,tvl,volume,version="v3"):
    a,b=pair.split("/")
    return {
        "chainId":"ethereum",
        "pairAddress":address,
        "dexId":"uniswap",
        "labels":[version],
        "baseToken":{"address":"0x"+"3"*40,"symbol":a,"name":a},
        "quoteToken":{"address":"0x"+"4"*40,"symbol":b,"name":b},
        "liquidity":{"usd":tvl},
        "volume":{"h24":volume},
    }


def test_candidate_universe_filters_then_validates_and_marks_portfolio_overlap():
    a="0x"+"a"*40
    b="0x"+"b"*40
    c="0x"+"c"*40
    graph=[
        pool(a,"CASHCAT/WETH",400_000,900_000,fees=1000),
        pool(b,"NEW/WETH",250_000,600_000,fees=800),
        pool(c,"DUST/WETH",2_000,1_000,fees=2),
    ]
    live_a={**pool(a,"CASHCAT/WETH",390_000,920_000,source="DEXSCREENER"),"protocol_guess":"UNISWAP_V3"}
    discovery=FakeDiscovery(graph=graph,dex=[live_a],exact={b:dex_pair(b,"NEW/WETH",240_000,650_000)})
    store=FakeStore()
    store.positions=[{
        "id":"P10","status":"OPEN","chain":"ETHEREUM","pair":"WETH/CASHCAT",
        "pool_address":"0x"+"9"*40,"current_value":250,
    }]
    universe=CandidateUniverse(SimpleNamespace(),store,discovery)

    result=universe.refresh("ETHEREUM",graph_limit=500,shortlist_limit=40,validate_limit=20)

    assert result["feeds_strategy"] is True
    assert result["feeds_portfolio_advisor"] is True
    assert result["summary"]["v3_discovered"]==3
    assert result["summary"]["cheap_filtered_out"]==1
    assert result["summary"]["shortlisted"]==2
    assert result["summary"]["live_validated"]==2
    assert result["summary"]["portfolio_overlaps"]==1
    assert result["summary"]["targeted_live_requests"]==1
    assert len(discovery.exact_calls)==1

    cashcat=next(x for x in result["shortlist"] if x["pair"]=="CASHCAT/WETH")
    new=next(x for x in result["shortlist"] if x["pair"]=="NEW/WETH")
    assert cashcat["portfolio_overlap"] is True
    assert "CASHCAT" in cashcat["portfolio_matching_assets"]
    assert new["targeted_live_validation"] is True
    assert new["economic_validation"]=="CROSS_VALIDATED"
    assert result["summary"]["research_ready"]==2
    assert store.settings["candidate_universe:ETHEREUM"]["summary"]["shortlisted"]==2


def test_robinhood_can_build_from_live_sources_without_graph():
    a="0x"+"d"*40
    live={**pool(a,"HOOKR/WETH",80_000,150_000,source="DEXSCREENER"),"chain":"ROBINHOOD_CHAIN","protocol_guess":"UNISWAP_V3"}
    discovery=FakeDiscovery(dex=[live])
    store=FakeStore()
    universe=CandidateUniverse(SimpleNamespace(),store,discovery)

    result=universe.refresh("ROBINHOOD_CHAIN",shortlist_limit=20,validate_limit=10)

    assert result["ok"] is True
    assert result["summary"]["v3_discovered"]==1
    assert result["summary"]["live_validated"]==1
    assert result["summary"]["targeted_live_requests"]==0
    assert result["shortlist"][0]["economic_validation"]=="LIVE_VALIDATED"


def test_cached_universe_returns_persisted_snapshot():
    store=FakeStore()
    store.settings["candidate_universe:BASE"]={"ok":True,"chain":"BASE","summary":{"shortlisted":12},"shortlist":[]}
    universe=CandidateUniverse(SimpleNamespace(),store,FakeDiscovery())

    result=universe.cached("BASE")

    assert result["cached"] is True
    assert result["summary"]["shortlisted"]==12


def test_discovery_lab_prefers_remembered_working_subgraph(monkeypatch):
    store=FakeStore()
    store.settings["discovery:working_subgraph:OPTIMISM"]="remembered-good-id"
    monkeypatch.delenv("THEGRAPH_UNISWAP_V3_OPTIMISM_SUBGRAPH_ID",raising=False)
    lab=DiscoveryLab(SimpleNamespace(thegraph_api_key="x"),store=store)

    ids=lab._graph_ids("OPTIMISM")

    assert ids[0]=="remembered-good-id"
    assert len(ids)>1


def test_v0964_ui_promotes_candidate_universe_to_shared_scout_and_advisor_source():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert "v0.9.6.4.1 · stability hotfix" in html
    assert "Candidate Universe · v0.9.6.4.1" in html
    assert "SHARED DISCOVERY · FEEDS ADVISOR" in html
    assert 'id="universe-run-btn"' in html
    assert "/api/candidate-universe/" in js
    assert "Discovery score only decides which pools deserve scarce live checks" in js
    assert "shared discovery source for Scout and Portfolio Advisor" in js
