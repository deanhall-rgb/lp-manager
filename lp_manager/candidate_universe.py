from __future__ import annotations

import math
import time
from typing import Any

from .chain_registry import CHAINS
from .discovery_lab import _addr, _f, _normalise_dex_pair
from .live_scout import MAJORS


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _symbols(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("base_token", "quote_token"):
        symbol = str((row.get(key) or {}).get("symbol") or "").strip().upper()
        if symbol and symbol != "?":
            out.add(symbol)
    for part in str(row.get("pair") or "").replace("-", "/").split("/"):
        symbol = part.strip().upper()
        if symbol and symbol != "?":
            out.add(symbol)
    return out


def _pair_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(_symbols(row)))


def _is_v3(row: dict[str, Any]) -> bool:
    return (
        str(row.get("version") or "").upper() == "V3"
        or str(row.get("protocol") or "").upper() == "UNISWAP_V3"
        or str(row.get("protocol_guess") or "").upper() == "UNISWAP_V3"
    )


def _explicitly_unsupported(row: dict[str, Any]) -> bool:
    return str(row.get("version") or "").upper() in {"V2", "V4"}


def _log_score(value: float, floor: float, decades: float = 4.0) -> float:
    value=max(0.0,float(value or 0))
    if value <= floor:
        return 0.0
    return _clamp(math.log10(value / floor) / max(decades, 0.1) * 100.0)


class CandidateUniverse:
    """v0.9.6.2 broad discovery + cheap filtering + selective validation.

    This surface is intentionally isolated from the existing Opportunity Scout,
    Portfolio Advisor and Profit Lab. It proves that a much broader V3 universe
    can be considered without spending live-provider calls on every pool.
    """

    def __init__(self, settings, store, discovery_lab):
        self.settings=settings
        self.store=store
        self.discovery=discovery_lab

    def _portfolio_context(self, chain_key: str) -> dict[str, Any]:
        try:
            rows=[dict(x) for x in self.store.list_positions("OPEN")]
        except Exception:
            rows=[]
        rows=[x for x in rows if str(x.get("chain") or "").upper()==chain_key]
        pools: dict[str,list[dict[str,Any]]]={}
        pairs: dict[tuple[str,...],list[dict[str,Any]]]={}
        assets: dict[str,list[dict[str,Any]]]={}
        for row in rows:
            pool=_addr(row.get("pool_address"))
            if pool:
                pools.setdefault(pool,[]).append(row)
            key=_pair_key(row)
            if key:
                pairs.setdefault(key,[]).append(row)
            for symbol in _symbols(row):
                if symbol not in MAJORS:
                    assets.setdefault(symbol,[]).append(row)
        return {"rows":rows,"pools":pools,"pairs":pairs,"assets":assets}

    @staticmethod
    def _merge_candidate(address: str, by_source: dict[str,dict[str,Any]]) -> dict[str,Any]:
        graph=by_source.get("THEGRAPH")
        dex=by_source.get("DEXSCREENER")
        gecko=by_source.get("GECKOTERMINAL")
        live=[x for x in (dex,gecko) if x]
        chosen=dict(dex or gecko or graph or {})
        if graph:
            for key in (
                "fee_tier","fees_24h_usd","activity_day_ts","activity_day_tx_count",
                "total_volume_usd","base_token","quote_token","pair",
            ):
                if chosen.get(key) in (None,"",0,{},[]):
                    chosen[key]=graph.get(key)
            if str(chosen.get("version") or "").upper() in {"","UNKNOWN","NONE"}:
                chosen["version"]="V3"
            if str(chosen.get("protocol_guess") or "").upper() in {"","UNKNOWN","UNISWAP","NONE"}:
                chosen["protocol_guess"]="UNISWAP_V3"

        chosen["pool_address"]=chosen.get("pool_address") or address
        chosen["protocol"]="UNISWAP_V3" if _is_v3(chosen) else chosen.get("protocol")
        providers=sorted(by_source)
        chosen["providers"]=providers
        chosen["provider_count"]=len(providers)

        graph_tvl=_f(graph.get("tvl_usd")) if graph else 0.0
        live_tvl=max((_f(x.get("tvl_usd")) for x in live),default=0.0)
        mismatch=False
        if graph_tvl>0 and live_tvl>0:
            mismatch=max(graph_tvl,live_tvl)/max(min(graph_tvl,live_tvl),1e-9) >= 5.0

        if live:
            if mismatch:
                validation="TVL_MISMATCH"
            elif graph or len(live)>1:
                validation="CROSS_VALIDATED"
            else:
                validation="LIVE_VALIDATED"
        else:
            validation="UNVERIFIED"

        chosen["economic_validation"]=validation
        chosen["graph_tvl_usd"]=graph_tvl if graph else None
        chosen["live_tvl_usd"]=live_tvl if live else None
        chosen["graph_discovered"]=bool(graph)
        chosen["live_discovered"]=bool(live)
        return chosen

    @staticmethod
    def _discovery_metrics(row: dict[str,Any]) -> dict[str,Any]:
        tvl=max(0.0,_f(row.get("tvl_usd")))
        volume=max(0.0,_f(row.get("volume_24h_usd")))
        fees=max(0.0,_f(row.get("fees_24h_usd")))
        turnover=volume/max(tvl,1.0)
        gross_fee_apr=(fees*365.0/tvl*100.0) if fees>0 and tvl>0 else 0.0
        tvl_score=_log_score(tvl,10_000.0)
        volume_score=_log_score(volume,10_000.0)
        turnover_score=_clamp(turnover/2.5*100.0)
        fee_score=_clamp(gross_fee_apr/300.0*100.0) if gross_fee_apr>0 else 35.0
        score=0.20*tvl_score + 0.40*volume_score + 0.25*turnover_score + 0.15*fee_score
        return {
            "discovery_score":round(score,1),
            "turnover_24h":round(turnover,3),
            "gross_fee_apr_proxy":round(gross_fee_apr,1),
        }

    @staticmethod
    def _attach_portfolio(row: dict[str,Any], ctx: dict[str,Any]) -> dict[str,Any]:
        address=_addr(row.get("pool_address"))
        exact=list(ctx["pools"].get(address) or [])
        same_pair=list(ctx["pairs"].get(_pair_key(row)) or [])
        asset_rows=[]
        matched_assets=[]
        for symbol in sorted(_symbols(row)):
            if symbol in MAJORS:
                continue
            matches=ctx["assets"].get(symbol) or []
            if matches:
                matched_assets.append(symbol)
                asset_rows.extend(matches)
        unique={str(x.get("id") or id(x)):x for x in [*exact,*same_pair,*asset_rows]}
        exposure=sum(float(x.get("current_value") or x.get("capital_value") or 0) for x in unique.values())
        reasons=[]
        if exact:
            reasons.append("SAME_POOL")
        elif same_pair:
            reasons.append("SAME_PAIR")
        if matched_assets:
            reasons.append("ASSET_"+"_".join(matched_assets[:2]))
        row["portfolio_overlap"]=bool(unique)
        row["portfolio_match_reasons"]=reasons
        row["portfolio_matching_assets"]=matched_assets
        row["portfolio_open_positions"]=len(unique)
        row["portfolio_exposure"]=round(exposure,2)
        return row

    def _broad_candidates(self, chain_key: str, graph_limit: int) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
        graph=self.discovery.graph_sample(chain_key,graph_limit)
        dex=self.discovery.dexscreener_sample(chain_key)
        # One Gecko page is enough to seed live validation. It is deliberately
        # not used as the exhaustive discovery backbone.
        gecko=self.discovery.gecko_sample(chain_key,1)
        providers=[graph,dex,gecko]

        grouped: dict[str,dict[str,dict[str,Any]]]={}
        for provider in providers:
            source=str(provider.get("provider") or "")
            for raw in provider.get("candidates") or []:
                address=_addr(raw.get("pool_address"))
                if not address:
                    continue
                grouped.setdefault(address,{})[source]=dict(raw)

        rows=[]
        for address,sources in grouped.items():
            row=self._merge_candidate(address,sources)
            if _explicitly_unsupported(row) and not sources.get("THEGRAPH"):
                continue
            if not _is_v3(row):
                continue
            row.update(self._discovery_metrics(row))
            rows.append(row)
        return rows,providers

    def _cheap_filter(self, rows: list[dict[str,Any]], ctx: dict[str,Any], shortlist_limit: int) -> tuple[list[dict[str,Any]],int]:
        kept=[]
        filtered=0
        for original in rows:
            row=self._attach_portfolio(dict(original),ctx)
            tvl=max(0.0,_f(row.get("tvl_usd")))
            volume=max(0.0,_f(row.get("volume_24h_usd")))
            symbols=_symbols(row)
            if not _addr(row.get("pool_address")) or len(_addr(row.get("pool_address")))!=42:
                filtered+=1
                continue
            if len(symbols)<2:
                filtered+=1
                continue
            # Broad but not indiscriminate: tiny/inactive pools do not deserve a
            # scarce live-provider lookup unless they already overlap our book.
            if not row.get("portfolio_overlap") and tvl < 10_000 and volume < 25_000:
                filtered+=1
                continue
            kept.append(row)
        kept.sort(
            key=lambda x:(
                bool(x.get("portfolio_overlap")),
                float(x.get("discovery_score") or 0),
                _f(x.get("volume_24h_usd")),
            ),
            reverse=True,
        )
        return kept[:max(5,min(100,int(shortlist_limit)))],filtered

    def _targeted_validate(self, chain_key: str, shortlist: list[dict[str,Any]], validate_limit: int) -> tuple[int,list[str]]:
        requests_used=0
        errors=[]
        limit=max(0,min(50,int(validate_limit)))
        for row in shortlist[:limit]:
            if str(row.get("economic_validation") or "") in {"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"}:
                continue
            pair,error=self.discovery._dex_pair_lookup(chain_key,str(row.get("pool_address") or ""))
            requests_used+=1
            if error:
                errors.append(error[:180])
                continue
            if not pair:
                continue
            live=_normalise_dex_pair(pair,chain_key)
            live_version=str(live.get("version") or "").upper()
            if live_version in {"V2","V4"}:
                row["economic_validation"]="VERSION_CONFLICT"
                row["version_conflict"]=live_version
                continue

            graph_tvl=_f(row.get("tvl_usd"))
            live_tvl=_f(live.get("tvl_usd"))
            mismatch=False
            if graph_tvl>0 and live_tvl>0:
                mismatch=max(graph_tvl,live_tvl)/max(min(graph_tvl,live_tvl),1e-9) >= 5.0

            for key in ("pair","base_token","quote_token","tvl_usd","volume_24h_usd","dex_id","source_updated_at"):
                if live.get(key) not in (None,"",{},[]):
                    row[key]=live.get(key)
            row["version"]="V3"
            row["protocol"]="UNISWAP_V3"
            row["protocol_guess"]="UNISWAP_V3"
            providers=set(row.get("providers") or [])
            providers.add("DEXSCREENER_TARGETED")
            row["providers"]=sorted(providers)
            row["provider_count"]=len(providers)
            row["live_tvl_usd"]=live_tvl or None
            row["economic_validation"]="TVL_MISMATCH" if mismatch else "CROSS_VALIDATED"
            row["targeted_live_validation"]=True
            row.update(self._discovery_metrics(row))
        return requests_used,errors

    @staticmethod
    def _research_ready(row: dict[str,Any]) -> bool:
        validation=str(row.get("economic_validation") or "")
        return (
            validation in {"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"}
            and _f(row.get("tvl_usd")) >= 25_000
            and _f(row.get("volume_24h_usd")) >= 10_000
            and not row.get("version_conflict")
        )

    def refresh(self, chain_key: str, *, graph_limit: int = 500, shortlist_limit: int = 40, validate_limit: int = 20) -> dict[str,Any]:
        chain_key=str(chain_key or "").upper()
        if chain_key not in CHAINS:
            raise ValueError(f"Unsupported chain: {chain_key}")
        started=time.perf_counter()
        rows,providers=self._broad_candidates(chain_key,max(50,min(1000,int(graph_limit))))
        ctx=self._portfolio_context(chain_key)
        shortlist,filtered=self._cheap_filter(rows,ctx,shortlist_limit)
        targeted_requests,target_errors=self._targeted_validate(chain_key,shortlist,validate_limit)

        for row in shortlist:
            row["research_ready"]=self._research_ready(row)
        shortlist.sort(
            key=lambda x:(
                bool(x.get("research_ready")),
                str(x.get("economic_validation") or "")!="UNVERIFIED",
                float(x.get("discovery_score") or 0),
            ),
            reverse=True,
        )

        live_validated=sum(1 for x in shortlist if str(x.get("economic_validation") or "") in {"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"})
        awaiting=sum(1 for x in shortlist if str(x.get("economic_validation") or "")=="UNVERIFIED")
        overlaps=sum(1 for x in shortlist if x.get("portfolio_overlap"))
        ready=sum(1 for x in shortlist if x.get("research_ready"))
        conflicts=sum(1 for x in shortlist if str(x.get("economic_validation") or "")=="VERSION_CONFLICT")

        provider_summary=[{
            "provider":p.get("provider"),
            "status":p.get("status"),
            "candidate_count":p.get("candidate_count",len(p.get("candidates") or [])),
            "requests":p.get("requests",0),
            "elapsed_ms":p.get("elapsed_ms",0),
            "subgraph_id":p.get("subgraph_id"),
            "fallback_used":p.get("fallback_used",False),
            "error":p.get("error"),
        } for p in providers]

        snapshot={
            "ok":bool(rows),
            "mode":"READ_ONLY_CANDIDATE_UNIVERSE",
            "feeds_strategy":False,
            "feeds_portfolio_advisor":False,
            "chain":chain_key,
            "generated_at":time.time(),
            "graph_limit":int(graph_limit),
            "shortlist_limit":int(shortlist_limit),
            "validate_limit":int(validate_limit),
            "summary":{
                "v3_discovered":len(rows),
                "cheap_filtered_out":filtered,
                "shortlisted":len(shortlist),
                "live_validated":live_validated,
                "awaiting_live_validation":awaiting,
                "research_ready":ready,
                "portfolio_overlaps":overlaps,
                "version_conflicts":conflicts,
                "targeted_live_requests":targeted_requests,
                "elapsed_ms":round((time.perf_counter()-started)*1000,1),
            },
            "providers":provider_summary,
            "shortlist":shortlist,
            "targeted_validation_errors":target_errors[:5],
            "note":"Discovery score is a cheap search heuristic only. It does not rank capital or change Portfolio Advisor / Profit Lab.",
        }
        try:
            self.store.set_setting(f"candidate_universe:{chain_key}",snapshot)
        except Exception:
            pass
        return snapshot

    def cached(self, chain_key: str) -> dict[str,Any]:
        chain_key=str(chain_key or "").upper()
        if chain_key not in CHAINS:
            raise ValueError(f"Unsupported chain: {chain_key}")
        try:
            payload=self.store.get_setting(f"candidate_universe:{chain_key}",None)
        except Exception:
            payload=None
        if isinstance(payload,dict) and payload:
            return {**payload,"cached":True}
        return {
            "ok":False,
            "cached":True,
            "mode":"READ_ONLY_CANDIDATE_UNIVERSE",
            "feeds_strategy":False,
            "feeds_portfolio_advisor":False,
            "chain":chain_key,
            "summary":{},
            "providers":[],
            "shortlist":[],
            "note":"No candidate universe has been built for this chain yet.",
        }
