from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import lp_manager.discovery_lab as discovery
from lp_manager.discovery_lab import DEFAULT_SUBGRAPH_IDS, SUBGRAPH_CANDIDATES, DiscoveryLab


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload=payload
        self.status_code=status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *, get_map=None, post_payload=None, post_map=None):
        self.get_map=get_map or {}
        self.post_payload=post_payload or {}
        self.post_map=post_map or {}
        self.get_calls=[]
        self.post_calls=[]
        self.headers={}

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        for key,value in self.get_map.items():
            if key in url:
                if callable(value):
                    return value(url)
                return FakeResponse(value)
        return FakeResponse({},404)

    def post(self, url, **kwargs):
        self.post_calls.append((url,kwargs))
        for key,value in self.post_map.items():
            if key in url:
                if callable(value):
                    value=value(url,kwargs)
                if isinstance(value,FakeResponse):
                    return value
                return FakeResponse(value)
        return FakeResponse(self.post_payload)


class FakeMarket:
    def __init__(self):
        self.calls=[]

    def network_pools(self, chain, page=1):
        self.calls.append((chain,page))
        return [
            {
                "chain":chain,
                "pool_address":"0x00000000000000000000000000000000000000a1",
                "pair":"WETH/USDC",
                "protocol":"UNISWAP_V3",
                "tvl_usd":1_000_000,
                "volume_24h_usd":500_000,
                "source":"GECKOTERMINAL",
            }
        ]


def settings(graph_key=""):
    return SimpleNamespace(thegraph_api_key=graph_key)


def test_discovery_lab_is_read_only_and_unions_provider_samples():
    market=FakeMarket()
    dex_pair={
        "chainId":"ethereum",
        "dexId":"uniswap",
        "labels":["v3"],
        "pairAddress":"0x00000000000000000000000000000000000000a1",
        "baseToken":{"address":"0x1","symbol":"WETH","name":"Wrapped Ether"},
        "quoteToken":{"address":"0x2","symbol":"USDC","name":"USD Coin"},
        "liquidity":{"usd":1_100_000},
        "volume":{"h24":550_000},
    }
    session=FakeSession(get_map={"/token-pairs/v1/ethereum/":[dex_pair]})
    lab=DiscoveryLab(settings(),market,session)

    result=lab.run("ETHEREUM",gecko_pages=1,graph_limit=100)

    assert result["mode"]=="READ_ONLY_DISCOVERY_PROOF"
    assert result["feeds_strategy"] is False
    assert result["providers"][0]["provider"]=="GECKOTERMINAL"
    assert result["providers"][0]["candidate_count"]==1
    assert result["providers"][1]["provider"]=="DEXSCREENER"
    assert result["providers"][1]["candidate_count"]==1
    assert result["providers"][2]["status"]=="NOT_CONFIGURED"
    assert result["union"]["unique_candidates"]==1
    assert result["union"]["multi_provider_matches"]==1
    assert market.calls==[("ETHEREUM",1)]


def test_thegraph_sample_can_return_large_candidate_page_without_strategy_side_effects():
    payload={
        "data":{
            "pools":[
                {
                    "id":"0x00000000000000000000000000000000000000b1",
                    "feeTier":"3000",
                    "liquidity":"1",
                    "totalValueLockedUSD":"12000000",
                    "volumeUSD":"500000000",
                    "poolDayData":[{"date":1790630400,"volumeUSD":"4200000","feesUSD":"12600","tvlUSD":"11900000","txCount":"1234"}],
                    "token0":{"id":"0x1","symbol":"WETH","name":"Wrapped Ether","decimals":"18"},
                    "token1":{"id":"0x2","symbol":"USDC","name":"USD Coin","decimals":"6"},
                },
                {
                    "id":"0x00000000000000000000000000000000000000b2",
                    "feeTier":"500",
                    "liquidity":"1",
                    "totalValueLockedUSD":"8000000",
                    "volumeUSD":"300000000",
                    "poolDayData":[{"date":1790630400,"volumeUSD":"2100000","feesUSD":"1050","tvlUSD":"7900000","txCount":"456"}],
                    "token0":{"id":"0x3","symbol":"WBTC","name":"Wrapped BTC","decimals":"8"},
                    "token1":{"id":"0x1","symbol":"WETH","name":"Wrapped Ether","decimals":"18"},
                },
            ]
        }
    }
    session=FakeSession(post_payload=payload)
    lab=DiscoveryLab(settings("graph-key"),FakeMarket(),session)

    result=lab.graph_sample("ETHEREUM",limit=250)

    assert result["status"]=="OK"
    assert result["requests"]==1
    assert result["candidate_count"]==2
    assert result["candidates"][0]["protocol_guess"]=="UNISWAP_V3"
    assert result["candidates"][0]["fee_tier"]==3000
    assert result["candidates"][0]["volume_24h_usd"]==4_200_000
    assert result["candidates"][0]["fees_24h_usd"]==12_600
    assert session.post_calls
    assert session.post_calls[0][1]["json"]["variables"]["first"]==250
    assert session.post_calls[0][1]["headers"]["Authorization"]=="Bearer graph-key"


def test_exact_resolver_finds_wrong_chain_and_explains_uniswap_v2(monkeypatch):
    shib="0x811beed0119b4afce20d2583eb608c6f7af1954f"
    pair={
        "chainId":"ethereum",
        "dexId":"uniswap",
        "labels":["v2"],
        "pairAddress":shib,
        "baseToken":{"address":"0x1","symbol":"SHIB","name":"Shiba Inu"},
        "quoteToken":{"address":"0x2","symbol":"WETH","name":"Wrapped Ether"},
        "liquidity":{"usd":1000000},
        "volume":{"h24":100000},
    }
    session=FakeSession(get_map={
        "/latest/dex/pairs/base/":{},
        "/latest/dex/pairs/ethereum/":{"pairs":[pair]},
    })
    lab=DiscoveryLab(settings(),FakeMarket(),session)

    result=lab.resolve_pool("BASE",shib)

    assert result["ok"] is True
    assert result["resolved_chain"]=="ETHEREUM"
    assert result["chain_mismatch"] is True
    assert result["status"]=="UNSUPPORTED_V2"
    assert result["supported"] is False
    assert "Uniswap V2" in result["message"]
    assert result["requests"]==2


def test_exact_resolver_verifies_uniswap_v3_onchain(monkeypatch):
    pool="0x0000000000000000000000000000000000000abc"
    pair={
        "chainId":"base",
        "dexId":"uniswap",
        "labels":["v3"],
        "pairAddress":pool,
        "baseToken":{"address":"0x1","symbol":"WETH","name":"Wrapped Ether"},
        "quoteToken":{"address":"0x2","symbol":"USDC","name":"USD Coin"},
        "liquidity":{"usd":5000000},
        "volume":{"h24":2000000},
    }
    session=FakeSession(get_map={"/latest/dex/pairs/base/":{"pairs":[pair]}})
    monkeypatch.setattr(discovery,"read_v3_pool_metadata",lambda chain,address:{"ok":True,"chain":chain,"pool_address":address,"fee_tier":3000})
    lab=DiscoveryLab(settings(),FakeMarket(),session)

    result=lab.resolve_pool("BASE",pool)

    assert result["status"]=="SUPPORTED_V3"
    assert result["supported"] is True
    assert result["resolved_chain"]=="BASE"
    assert result["onchain"]["fee_tier"]==3000
    assert result["source"]=="DEXSCREENER + ONCHAIN_V3_VERIFICATION"


def test_discovery_lab_ui_is_explicitly_non_strategy_and_has_exact_pool_resolver():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert "Discovery Lab · v0.9.6.1" in html
    assert "DOES NOT FEED STRATEGY" in html
    assert 'id="discovery-run-btn"' in html
    assert 'id="discovery-resolve-btn"' in html
    assert "Run provider test" in html
    assert "Exact pool resolver" in html
    assert "function runDiscoveryLab()" in js
    assert "function resolveDiscoveryPool()" in js
    assert "/api/discovery-lab/" in js
    assert "This run did not save opportunities" in js


def test_optimism_and_polygon_have_known_graph_defaults():
    assert DEFAULT_SUBGRAPH_IDS["OPTIMISM"]=="EgnS9YE1avupkvCNj9fHnJxppfEmNNywYJtghqiu2pd9"
    assert DEFAULT_SUBGRAPH_IDS["POLYGON"]=="EsLGwxyeMMeJuhqWvuLmJEiDKXJ4Z6YsoJreUnyeozco"
    assert len(SUBGRAPH_CANDIDATES["OPTIMISM"])>=2
    assert len(SUBGRAPH_CANDIDATES["ARBITRUM"])>=2


def test_union_prefers_live_economics_and_flags_absurd_graph_tvl():
    address="0x0000000000000000000000000000000000000c01"
    graph={
        "provider":"THEGRAPH","status":"OK",
        "candidates":[{
            "pool_address":address,"pair":"ODD/WETH","version":"V3",
            "protocol_guess":"UNISWAP_V3","tvl_usd":1_000_000_000_000,
            "volume_24h_usd":5_000_000,
        }],
    }
    dex={
        "provider":"DEXSCREENER","status":"OK",
        "candidates":[{
            "pool_address":address,"pair":"ODD/WETH","version":"V3",
            "tvl_usd":2_000_000,"volume_24h_usd":900_000,
        }],
    }

    result=DiscoveryLab._union([graph,dex])
    row=result["top_candidates"][0]

    assert result["unique_candidates"]==1
    assert result["live_validated_candidates"]==1
    assert result["tvl_mismatch_count"]==1
    assert row["economic_validation"]=="TVL_MISMATCH"
    assert row["tvl_usd"]==2_000_000
    assert row["graph_tvl_usd"]==1_000_000_000_000
    assert row["live_tvl_usd"]==2_000_000


def test_graph_only_pool_is_unverified_not_rank_ready():
    address="0x0000000000000000000000000000000000000c02"
    graph={
        "provider":"THEGRAPH","status":"OK",
        "candidates":[{
            "pool_address":address,"pair":"GRAPH/ONLY","version":"V3",
            "protocol_guess":"UNISWAP_V3","tvl_usd":900_000_000_000,
            "volume_24h_usd":1_000,
        }],
    }

    result=DiscoveryLab._union([graph])
    row=result["top_candidates"][0]

    assert result["graph_only_candidates"]==1
    assert result["live_validated_candidates"]==0
    assert row["economic_validation"]=="UNVERIFIED"
    assert "live-provider validation" in row["economic_validation_reason"]


def test_v0961_patch_ui_shows_validation_funnel_and_recent_daily_volume():
    root=Path(__file__).parents[1]
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert "discovered →" in js
    assert "V3 live validated" in js
    assert "V3 awaiting live validation" in js
    assert "TVL mismatches" in js
    assert "Recent daily volume" in js
    assert "Graph-only candidates remain unverified" in js


def test_graph_sample_falls_back_when_preferred_schema_has_no_pools(monkeypatch):
    bad=SUBGRAPH_CANDIDATES["ARBITRUM"][0]
    good=SUBGRAPH_CANDIDATES["ARBITRUM"][1]
    good_payload={
        "data":{
            "pools":[{
                "id":"0x0000000000000000000000000000000000000d01",
                "feeTier":"3000",
                "liquidity":"1",
                "totalValueLockedUSD":"2500000",
                "volumeUSD":"100000000",
                "poolDayData":[{"date":1790630400,"volumeUSD":"1500000","feesUSD":"4500","tvlUSD":"2500000","txCount":"500"}],
                "token0":{"id":"0x1","symbol":"WETH","name":"Wrapped Ether","decimals":"18"},
                "token1":{"id":"0x2","symbol":"USDC","name":"USD Coin","decimals":"6"},
            }]
        }
    }
    session=FakeSession(post_map={
        bad:{"errors":[{"message":"Type 'Query' has no field 'pools'"}]},
        good:good_payload,
    })
    lab=DiscoveryLab(settings("graph-key"),FakeMarket(),session)

    result=lab.graph_sample("ARBITRUM",limit=500)

    assert result["status"]=="OK"
    assert result["fallback_used"] is True
    assert result["requests"]==2
    assert result["subgraph_id"]==good
    assert result["candidate_count"]==1
    assert result["candidates"][0]["volume_24h_usd"]==1_500_000


def test_union_promotes_graph_v3_identity_when_live_provider_version_is_unknown():
    address="0x0000000000000000000000000000000000000d02"
    graph={
        "provider":"THEGRAPH","status":"OK",
        "candidates":[{
            "pool_address":address,"pair":"WETH/USDC","version":"V3",
            "protocol_guess":"UNISWAP_V3","tvl_usd":900_000,
            "volume_24h_usd":400_000,
        }],
    }
    dex={
        "provider":"DEXSCREENER","status":"OK",
        "candidates":[{
            "pool_address":address,"pair":"WETH/USDC","version":"UNKNOWN",
            "protocol_guess":"UNISWAP","tvl_usd":850_000,
            "volume_24h_usd":450_000,
        }],
    }

    result=DiscoveryLab._union([graph,dex])
    row=result["top_candidates"][0]

    assert row["version"]=="V3"
    assert row["protocol_guess"]=="UNISWAP_V3"
    assert result["v3_eligible_candidates"]==1
    assert result["live_validated_v3_candidates"]==1
    assert result["unknown_version_candidates"]==0


def test_union_excludes_v2_v4_and_unknown_from_v3_candidate_sample():
    rows=[
        {"pool_address":"0x0000000000000000000000000000000000000e01","pair":"A/B","version":"V3","tvl_usd":10,"volume_24h_usd":10},
        {"pool_address":"0x0000000000000000000000000000000000000e02","pair":"C/D","version":"V4","tvl_usd":20,"volume_24h_usd":20},
        {"pool_address":"0x0000000000000000000000000000000000000e03","pair":"E/F","version":"V2","tvl_usd":30,"volume_24h_usd":30},
        {"pool_address":"0x0000000000000000000000000000000000000e04","pair":"G/H","version":"UNKNOWN","tvl_usd":40,"volume_24h_usd":40},
    ]
    result=DiscoveryLab._union([{"provider":"DEXSCREENER","status":"OK","candidates":rows}])

    assert result["unique_candidates"]==4
    assert result["v3_eligible_candidates"]==1
    assert result["unsupported_version_candidates"]==2
    assert result["unknown_version_candidates"]==1
    assert len(result["top_candidates"])==1
    assert result["top_candidates"][0]["pair"]=="A/B"


def test_v0961_final_patch_ui_is_compact_and_v3_gated():
    root=Path(__file__).parents[1]
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert "V3 eligible →" in js
    assert "V2/V4 excluded" in js
    assert "Unknown version excluded" in js
    assert "V3 candidate sample" in js
    assert "Only V3-eligible pools are shown here" in js
    assert "Pool identity is seen by multiple providers; live-provider economics are preferred." not in js
    assert "Live economics used" in js
    assert "Needs live validation" in js
