from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import lp_manager.discovery_lab as discovery
from lp_manager.discovery_lab import DiscoveryLab


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
    def __init__(self, *, get_map=None, post_payload=None):
        self.get_map=get_map or {}
        self.post_payload=post_payload or {}
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
                    "token0":{"id":"0x1","symbol":"WETH","name":"Wrapped Ether","decimals":"18"},
                    "token1":{"id":"0x2","symbol":"USDC","name":"USD Coin","decimals":"6"},
                },
                {
                    "id":"0x00000000000000000000000000000000000000b2",
                    "feeTier":"500",
                    "liquidity":"1",
                    "totalValueLockedUSD":"8000000",
                    "volumeUSD":"300000000",
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
