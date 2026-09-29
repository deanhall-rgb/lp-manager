from __future__ import annotations

import os
import time
from typing import Any

import requests

from .chain_registry import CHAINS, chain_config
from .pool_chain import read_v3_pool_metadata


DEXSCREENER_BASE = "https://api.dexscreener.com"
THEGRAPH_BASE = "https://gateway.thegraph.com/api/subgraphs/id"

DEX_CHAIN = {
    "ETHEREUM": "ethereum",
    "BASE": "base",
    "ARBITRUM": "arbitrum",
    "OPTIMISM": "optimism",
    "POLYGON": "polygon",
    "ROBINHOOD_CHAIN": "robinhood",
}

# Anchor tokens are used only for the v0.9.6.1 discovery proof-of-concept.
# One token-pairs request can expose a much wider candidate surface than paging
# GeckoTerminal network-top results. This does not yet feed the strategy engine.
ANCHOR_TOKENS = {
    "ETHEREUM": [
        ("WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
        ("USDC", "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
    ],
    "BASE": [
        ("WETH", "0x4200000000000000000000000000000000000006"),
        ("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
    ],
    "ARBITRUM": [
        ("WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
        ("USDC", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),
    ],
    "OPTIMISM": [
        ("WETH", "0x4200000000000000000000000000000000000006"),
        ("USDC", "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"),
    ],
    "POLYGON": [
        ("WETH", "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"),
        ("USDC", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"),
    ],
    "ROBINHOOD_CHAIN": [
        ("WETH", "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"),
    ],
}

# Defaults are deliberately limited to subgraphs we have a concrete, indexed
# Graph Explorer reference for. Any chain can be overridden in .env without
# changing code: THEGRAPH_UNISWAP_V3_<CHAIN>_SUBGRAPH_ID=<id>
DEFAULT_SUBGRAPH_IDS = {
    "ETHEREUM": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "BASE": "VmwKeqb22QsuM4AcCp8qTFg2dW7EXZNg16kGgXu6bBu",
    "ARBITRUM": "FQ6JYszEKApsBpAmiHesRsd9Ygc6mzmpNRANeVQFYoVX",
    "OPTIMISM": "EgnS9YE1avupkvCNj9fHnJxppfEmNNywYJtghqiu2pd9",
    "POLYGON": "EsLGwxyeMMeJuhqWvuLmJEiDKXJ4Z6YsoJreUnyeozco",
}


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _addr(value: Any) -> str:
    return str(value or "").strip().lower()


def _dex_version(pair: dict[str, Any]) -> str:
    labels=[str(x or "").lower() for x in (pair.get("labels") or [])]
    for version in ("v4","v3","v2"):
        if version in labels:
            return version.upper()
    dex=str(pair.get("dexId") or "").lower()
    for version in ("v4","v3","v2"):
        if version in dex:
            return version.upper()
    return "UNKNOWN"


def _normalise_dex_pair(pair: dict[str, Any], chain_key: str) -> dict[str, Any]:
    base=pair.get("baseToken") or {}
    quote=pair.get("quoteToken") or {}
    liq=pair.get("liquidity") or {}
    vol=pair.get("volume") or {}
    version=_dex_version(pair)
    dex=str(pair.get("dexId") or "")
    return {
        "chain":chain_key,
        "pool_address":pair.get("pairAddress"),
        "pair":f"{base.get('symbol','?')}/{quote.get('symbol','?')}",
        "base_token":{"address":base.get("address"),"symbol":base.get("symbol"),"name":base.get("name")},
        "quote_token":{"address":quote.get("address"),"symbol":quote.get("symbol"),"name":quote.get("name")},
        "tvl_usd":_f(liq.get("usd")),
        "volume_24h_usd":_f(vol.get("h24")),
        "dex_id":dex,
        "version":version,
        "protocol_guess":f"{dex.upper()}_{version}" if version!="UNKNOWN" else dex.upper(),
        "source":"DEXSCREENER",
        "source_updated_at":time.time(),
    }


def _normalise_graph_pool(row: dict[str, Any], chain_key: str) -> dict[str, Any]:
    t0=row.get("token0") or {}
    t1=row.get("token1") or {}
    day_rows=row.get("poolDayData") or row.get("poolDayDatas") or []
    recent_day=day_rows[0] if isinstance(day_rows,list) and day_rows else {}
    recent_volume=_f(recent_day.get("volumeUSD")) if recent_day else None
    recent_fees=_f(recent_day.get("feesUSD")) if recent_day else None
    return {
        "chain":chain_key,
        "pool_address":row.get("id"),
        "pair":f"{t0.get('symbol','?')}/{t1.get('symbol','?')}",
        "base_token":{"address":t0.get("id"),"symbol":t0.get("symbol"),"name":t0.get("name")},
        "quote_token":{"address":t1.get("id"),"symbol":t1.get("symbol"),"name":t1.get("name")},
        "tvl_usd":_f(row.get("totalValueLockedUSD")),
        "volume_24h_usd":recent_volume,
        "fees_24h_usd":recent_fees,
        "activity_day_ts":int(_f(recent_day.get("date"))) if recent_day else None,
        "activity_day_tx_count":int(_f(recent_day.get("txCount"))) if recent_day else None,
        "total_volume_usd":_f(row.get("volumeUSD")),
        "fee_tier":int(_f(row.get("feeTier"))),
        "protocol_guess":"UNISWAP_V3",
        "version":"V3",
        "source":"THEGRAPH",
        "source_updated_at":time.time(),
        "economic_validation":"UNVERIFIED",
    }


class DiscoveryLab:
    """v0.9.6.1 read-only provider/discovery proof-of-concept.

    Nothing in this class writes opportunities, changes advisor ranking, or
    changes Profit Lab. It exists to measure provider coverage and prove the
    broader-discovery flow before we alter production strategy behaviour.
    """

    def __init__(self, settings, market=None, session: requests.Session | None = None):
        self.settings=settings
        self.market=market
        self.session=session or requests.Session()
        self.session.headers.update({"User-Agent":"LP-Manager/0.9.6.1","Accept":"application/json"})

    def _graph_id(self, chain_key: str) -> str:
        env=f"THEGRAPH_UNISWAP_V3_{chain_key}_SUBGRAPH_ID"
        return os.getenv(env,"").strip() or DEFAULT_SUBGRAPH_IDS.get(chain_key,"")

    def _graph_key(self) -> str:
        return str(getattr(self.settings,"thegraph_api_key","") or os.getenv("THEGRAPH_API_KEY","")).strip()

    def gecko_sample(self, chain_key: str, pages: int = 1) -> dict[str,Any]:
        started=time.perf_counter()
        if not self.market:
            return {"provider":"GECKOTERMINAL","status":"DISABLED","requests":0,"candidates":[],"elapsed_ms":0,"error":"Market client is disabled"}
        rows=[]
        errors=[]
        requests_used=0
        for page in range(1,max(1,min(2,int(pages)))+1):
            try:
                requests_used+=1
                rows.extend(self.market.network_pools(chain_key,page=page))
            except Exception as exc:
                errors.append(str(exc))
                break
        unique={_addr(x.get("pool_address")):x for x in rows if _addr(x.get("pool_address"))}
        return {
            "provider":"GECKOTERMINAL",
            "status":"OK" if unique else ("ERROR" if errors else "EMPTY"),
            "requests":requests_used,
            "rate_budget":"30/min public",
            "candidates":list(unique.values()),
            "candidate_count":len(unique),
            "elapsed_ms":round((time.perf_counter()-started)*1000,1),
            "error":"; ".join(errors)[:500] if errors else None,
        }

    def dexscreener_sample(self, chain_key: str) -> dict[str,Any]:
        started=time.perf_counter()
        slug=DEX_CHAIN.get(chain_key)
        if not slug:
            return {"provider":"DEXSCREENER","status":"UNSUPPORTED","requests":0,"candidates":[],"elapsed_ms":0,"error":"No chain mapping"}
        rows={}
        errors=[]
        requests_used=0
        for _,token in ANCHOR_TOKENS.get(chain_key,[])[:2]:
            try:
                requests_used+=1
                url=f"{DEXSCREENER_BASE}/token-pairs/v1/{slug}/{token}"
                r=self.session.get(url,timeout=15)
                r.raise_for_status()
                payload=r.json()
                pairs=payload if isinstance(payload,list) else (payload.get("pairs") or [])
                for pair in pairs:
                    if str(pair.get("chainId") or "").lower()!=slug:
                        continue
                    if "uniswap" not in str(pair.get("dexId") or "").lower():
                        continue
                    row=_normalise_dex_pair(pair,chain_key)
                    address=_addr(row.get("pool_address"))
                    if address:
                        rows[address]=row
            except Exception as exc:
                errors.append(str(exc))
        candidates=list(rows.values())
        candidates.sort(key=lambda x:(_f(x.get("tvl_usd")),_f(x.get("volume_24h_usd"))),reverse=True)
        return {
            "provider":"DEXSCREENER",
            "status":"OK" if candidates else ("ERROR" if errors else "EMPTY"),
            "requests":requests_used,
            "rate_budget":"300/min pair endpoints",
            "anchors":[x[0] for x in ANCHOR_TOKENS.get(chain_key,[])[:2]],
            "candidates":candidates,
            "candidate_count":len(candidates),
            "v3_candidate_count":sum(1 for x in candidates if x.get("version")=="V3"),
            "elapsed_ms":round((time.perf_counter()-started)*1000,1),
            "error":"; ".join(errors)[:500] if errors else None,
        }

    def graph_sample(self, chain_key: str, limit: int = 250) -> dict[str,Any]:
        started=time.perf_counter()
        key=self._graph_key()
        subgraph=self._graph_id(chain_key)
        if not key:
            return {
                "provider":"THEGRAPH","status":"NOT_CONFIGURED","requests":0,"candidates":[],
                "candidate_count":0,"elapsed_ms":0,
                "error":"Add THEGRAPH_API_KEY to .env to run this provider test.",
                "subgraph_id":subgraph or None,
            }
        if not subgraph:
            return {
                "provider":"THEGRAPH","status":"NO_SUBGRAPH","requests":0,"candidates":[],
                "candidate_count":0,"elapsed_ms":0,
                "error":f"Set THEGRAPH_UNISWAP_V3_{chain_key}_SUBGRAPH_ID in .env for this chain.",
            }
        query="""query DiscoveryPools($first: Int!) {
          pools(first: $first, orderBy: totalValueLockedUSD, orderDirection: desc) {
            id
            feeTier
            liquidity
            totalValueLockedUSD
            volumeUSD
            token0 { id symbol name decimals }
            token1 { id symbol name decimals }
            poolDayData(first: 2, orderBy: date, orderDirection: desc) {
              date
              volumeUSD
              feesUSD
              tvlUSD
              txCount
            }
          }
        }"""
        try:
            url=f"{THEGRAPH_BASE}/{subgraph}"
            r=self.session.post(
                url,
                json={"query":query,"variables":{"first":max(1,min(1000,int(limit)))}},
                headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                timeout=25,
            )
            r.raise_for_status()
            payload=r.json()
            if payload.get("errors"):
                raise RuntimeError("; ".join(str(x.get("message") or x) for x in payload["errors"][:3]))
            rows=[_normalise_graph_pool(x,chain_key) for x in ((payload.get("data") or {}).get("pools") or [])]
            return {
                "provider":"THEGRAPH","status":"OK" if rows else "EMPTY","requests":1,
                "rate_budget":"indexed query / API-key plan",
                "candidate_count":len(rows),"candidates":rows,
                "elapsed_ms":round((time.perf_counter()-started)*1000,1),
                "subgraph_id":subgraph,
                "error":None,
            }
        except Exception as exc:
            return {
                "provider":"THEGRAPH","status":"ERROR","requests":1,"candidate_count":0,"candidates":[],
                "elapsed_ms":round((time.perf_counter()-started)*1000,1),
                "subgraph_id":subgraph,"error":str(exc)[:500],
            }

    @staticmethod
    def _union(providers: list[dict[str,Any]]) -> dict[str,Any]:
        grouped: dict[str,list[tuple[str,dict[str,Any]]]]={}
        for provider in providers:
            name=str(provider.get("provider") or "")
            for row in provider.get("candidates") or []:
                address=_addr(row.get("pool_address"))
                if address:
                    grouped.setdefault(address,[]).append((name,dict(row)))

        out=[]
        mismatch_count=0
        live_validated=0
        graph_only=0
        for address,items in grouped.items():
            by_name={name:row for name,row in items}
            providers_here=sorted(by_name)
            graph=by_name.get("THEGRAPH")
            live=[by_name[x] for x in ("DEXSCREENER","GECKOTERMINAL") if x in by_name]

            # Prefer independently live market values for economics. The Graph is
            # excellent for broad discovery, but obscure-token USD pricing can be
            # nonsensical and must not outrank live-validated values.
            chosen=(
                by_name.get("DEXSCREENER")
                or by_name.get("GECKOTERMINAL")
                or graph
                or items[0][1]
            )
            row=dict(chosen)
            if graph:
                for key in ("fee_tier","protocol_guess","version","total_volume_usd","activity_day_ts","activity_day_tx_count","fees_24h_usd"):
                    if row.get(key) in (None,"",0) and graph.get(key) not in (None,""):
                        row[key]=graph.get(key)

            live_tvl=max((_f(x.get("tvl_usd")) for x in live),default=0.0)
            graph_tvl=_f(graph.get("tvl_usd")) if graph else 0.0
            mismatch=False
            if live_tvl>0 and graph_tvl>0:
                ratio=max(live_tvl,graph_tvl)/max(min(live_tvl,graph_tvl),1e-9)
                mismatch=ratio>=5.0

            if live:
                live_validated+=1
                if mismatch:
                    validation="TVL_MISMATCH"
                    mismatch_count+=1
                    reason="Graph TVL differs materially from a live provider; live-provider economics are displayed."
                elif len(providers_here)>=2:
                    validation="CROSS_VALIDATED"
                    reason="Pool identity is seen by multiple providers; live-provider economics are preferred."
                else:
                    validation="LIVE_VALIDATED"
                    reason="Live market provider supplied the displayed economics."
            else:
                graph_only+=1
                validation="UNVERIFIED"
                reason="Discovered by The Graph only; economics need live-provider validation before ranking."

            row["pool_address"]=row.get("pool_address") or address
            row["providers"]=providers_here
            row["provider_count"]=len(providers_here)
            row["economic_validation"]=validation
            row["economic_validation_reason"]=reason
            row["graph_tvl_usd"]=graph_tvl if graph else None
            row["live_tvl_usd"]=live_tvl if live else None
            out.append(row)

        # This remains a diagnostic sample, not a ranking. Sort validated rows
        # ahead of graph-only rows so implausible Graph USD values do not dominate
        # the screen merely because they are numerically enormous.
        status_order={"CROSS_VALIDATED":0,"LIVE_VALIDATED":1,"TVL_MISMATCH":2,"UNVERIFIED":3}
        out.sort(
            key=lambda x:(
                status_order.get(str(x.get("economic_validation")),9),
                -_f(x.get("volume_24h_usd")),
                -_f(x.get("tvl_usd")),
            )
        )
        overlaps=sum(1 for x in out if int(x.get("provider_count") or 0)>1)
        v3=sum(
            1 for x in out
            if str(x.get("version") or "").upper()=="V3"
            or str(x.get("protocol") or "").upper()=="UNISWAP_V3"
            or str(x.get("protocol_guess") or "").upper()=="UNISWAP_V3"
        )
        return {
            "unique_candidates":len(out),
            "multi_provider_matches":overlaps,
            "live_validated_candidates":live_validated,
            "graph_only_candidates":graph_only,
            "tvl_mismatch_count":mismatch_count,
            "v3_identified":v3,
            "top_candidates":out[:25],
        }

    def run(self, chain_key: str, *, gecko_pages: int = 1, graph_limit: int = 250) -> dict[str,Any]:
        chain_key=str(chain_key or "").upper()
        if chain_key not in CHAINS:
            raise ValueError(f"Unsupported chain: {chain_key}")
        started=time.perf_counter()
        providers=[
            self.gecko_sample(chain_key,gecko_pages),
            self.dexscreener_sample(chain_key),
            self.graph_sample(chain_key,graph_limit),
        ]
        union=self._union(providers)
        return {
            "ok":any(x.get("status")=="OK" for x in providers),
            "mode":"READ_ONLY_DISCOVERY_PROOF",
            "feeds_strategy":False,
            "chain":chain_key,
            "providers":providers,
            "union":union,
            "elapsed_ms":round((time.perf_counter()-started)*1000,1),
            "conclusion":"This lab measures coverage/provider behaviour only. It does not alter Opportunity Scout, Portfolio Advisor or Profit Lab.",
        }

    def _dex_pair_lookup(self, chain_key: str, address: str) -> tuple[dict[str,Any] | None, str | None]:
        slug=DEX_CHAIN.get(chain_key)
        if not slug:
            return None,"No DEX Screener chain mapping"
        try:
            r=self.session.get(f"{DEXSCREENER_BASE}/latest/dex/pairs/{slug}/{address}",timeout=12)
            if r.status_code==404:
                return None,None
            r.raise_for_status()
            payload=r.json()
            pairs=payload.get("pairs") or ([payload.get("pair")] if payload.get("pair") else [])
            pair=next((x for x in pairs if isinstance(x,dict) and _addr(x.get("pairAddress"))==_addr(address)),None)
            return pair,None
        except Exception as exc:
            return None,str(exc)

    def resolve_pool(self, preferred_chain: str, address: str) -> dict[str,Any]:
        preferred=str(preferred_chain or "").upper()
        address=str(address or "").strip()
        if preferred not in CHAINS:
            raise ValueError(f"Unsupported chain: {preferred}")
        if not address.startswith("0x") or len(address)!=42:
            raise ValueError("Enter a 42-character EVM pool address")

        order=[preferred]+[x for x in CHAINS if x!=preferred]
        errors=[]
        found_pair=None
        found_chain=None
        requests_used=0
        for chain_key in order:
            pair,error=self._dex_pair_lookup(chain_key,address)
            requests_used+=1
            if error:
                errors.append(f"{chain_key}: {error}")
                continue
            if pair:
                found_pair=pair
                found_chain=chain_key
                break

        if not found_pair or not found_chain:
            return {
                "ok":False,"address":address,"preferred_chain":preferred,
                "status":"NOT_FOUND","requests":requests_used,
                "message":"DEX Screener did not identify this address on the supported chains.",
                "errors":errors[:3],
            }

        row=_normalise_dex_pair(found_pair,found_chain)
        version=str(row.get("version") or "UNKNOWN")
        dex=str(row.get("dex_id") or "")
        supported=False
        onchain={}
        if "uniswap" in dex.lower() and version=="V3":
            try:
                onchain=read_v3_pool_metadata(found_chain,address)
                supported=bool(onchain.get("ok"))
            except Exception as exc:
                onchain={"ok":False,"error":str(exc)}
        elif "uniswap" in dex.lower() and version=="UNKNOWN":
            try:
                onchain=read_v3_pool_metadata(found_chain,address)
                supported=bool(onchain.get("ok"))
                if supported:
                    version="V3"
                    row["version"]="V3"
                    row["protocol_guess"]="UNISWAP_V3"
            except Exception as exc:
                onchain={"ok":False,"error":str(exc)}

        chain_mismatch=found_chain!=preferred
        if supported:
            message=f"Supported Uniswap V3 pool found on {found_chain}."
            if chain_mismatch:
                message+=f" The selected chain was {preferred}; LP Manager should switch to {found_chain} before analysis."
            status="SUPPORTED_V3"
        elif "uniswap" in dex.lower() and version in {"V2","V4"}:
            message=f"Pool found on {found_chain}, but DEX Screener identifies it as Uniswap {version}. LP Manager currently supports Uniswap V3 concentrated-liquidity pools only."
            status=f"UNSUPPORTED_{version}"
        else:
            message=f"Pool found on {found_chain}, but it is not verified as a supported Uniswap V3 pool."
            status="UNSUPPORTED_OR_UNKNOWN"

        return {
            "ok":True,
            "status":status,
            "address":address,
            "preferred_chain":preferred,
            "resolved_chain":found_chain,
            "chain_mismatch":chain_mismatch,
            "supported":supported,
            "dex_id":dex,
            "version":version,
            "pair":row.get("pair"),
            "tvl_usd":row.get("tvl_usd"),
            "volume_24h_usd":row.get("volume_24h_usd"),
            "requests":requests_used,
            "source":"DEXSCREENER + ONCHAIN_V3_VERIFICATION" if onchain else "DEXSCREENER",
            "onchain":onchain,
            "message":message,
            "errors":errors[:3],
        }
