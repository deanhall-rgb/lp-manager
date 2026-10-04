from __future__ import annotations

import math
import time
from typing import Any

from .chain_registry import CHAINS
from .discovery_lab import _addr, _f, _normalise_dex_pair
from .asset_registry import CORE_MAJOR_SYMBOLS
from .opportunity_model import canonical_opportunity, normalise_protocol, PROTOCOL_UNISWAP_V3
from .economics_engine import infer_fee_tier_bps
from .fee_metrics import pool_apr_24h_benchmark


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
    return normalise_protocol(
        row.get("protocol"),
        version=row.get("version"),
        protocol_guess=row.get("protocol_guess"),
        dex_id=row.get("dex_id"),
    ) == PROTOCOL_UNISWAP_V3


def _explicitly_unsupported(row: dict[str, Any]) -> bool:
    version=str(row.get("version") or "").upper()
    protocol=normalise_protocol(
        row.get("protocol"),
        version=version,
        protocol_guess=row.get("protocol_guess"),
        dex_id=row.get("dex_id"),
    )
    return version=="V2" or protocol=="UNISWAP_V4"


def _log_score(value: float, floor: float, decades: float = 4.0) -> float:
    value=max(0.0,float(value or 0))
    if value <= floor:
        return 0.0
    return _clamp(math.log10(value / floor) / max(decades, 0.1) * 100.0)


_PROFIT_STABLE_SYMBOLS={"USDC","USDT","USDT0","USDG","DAI","USDS","USDBC","FRAX","GHO"}


class CandidateUniverse:
    """Shared broad discovery + cheap filtering + selective validation.

    v0.9.6.2 proved the discovery funnel in isolation. v0.9.6.4 promotes that
    same shortlist to the Scout and Portfolio Advisor so every strategy surface
    starts from the same cross-chain candidate evidence. Profit Lab remains
    separate and still performs the expensive range/history analysis on demand.
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
                if symbol not in CORE_MAJOR_SYMBOLS:
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
        return canonical_opportunity(chosen, analysis_status="DISCOVERED")

    @staticmethod
    def _discovery_metrics(row: dict[str,Any]) -> dict[str,Any]:
        tvl=max(0.0,_f(row.get("tvl_usd")))
        volume=max(0.0,_f(row.get("volume_24h_usd")))
        turnover=volume/max(tvl,1.0)
        fee_bps,fee_source=infer_fee_tier_bps(row)
        # A pair-class assumption is useful inside modelling but is not strong
        # enough to populate a public leaderboard APR proxy. For the discovery
        # surface require explicit pool/name fee-tier evidence, or actual fees.
        benchmark_fee_bps=0.0 if fee_source=="PAIR_CLASS_ASSUMPTION" else fee_bps
        benchmark=pool_apr_24h_benchmark(row,fee_tier_bps=benchmark_fee_bps)
        gross_fee_apr=max(0.0,_f(benchmark.get("apr_pct"))) if benchmark.get("available") else 0.0
        tvl_score=_log_score(tvl,10_000.0)
        volume_score=_log_score(volume,10_000.0)
        turnover_score=_clamp(turnover/2.5*100.0)
        fee_score=_clamp(gross_fee_apr/300.0*100.0) if gross_fee_apr>0 else 35.0
        score=0.20*tvl_score + 0.40*volume_score + 0.25*turnover_score + 0.15*fee_score
        return {
            "discovery_score":round(score,1),
            "turnover_24h":round(turnover,3),
            "gross_fee_apr_proxy":round(gross_fee_apr,1),
            "fee_apr_proxy_evidence_class":str(benchmark.get("evidence_class") or "INSUFFICIENT_POOL_ECONOMICS"),
            "fee_apr_proxy_observed":bool(benchmark.get("observed")),
            "fee_apr_proxy_fee_tier_source":fee_source,
        }

    @staticmethod
    def _attach_portfolio(row: dict[str,Any], ctx: dict[str,Any]) -> dict[str,Any]:
        address=_addr(row.get("pool_address"))
        exact=list(ctx["pools"].get(address) or [])
        same_pair=list(ctx["pairs"].get(_pair_key(row)) or [])
        asset_rows=[]
        matched_assets=[]
        for symbol in sorted(_symbols(row)):
            if symbol in CORE_MAJOR_SYMBOLS:
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

    def _broad_candidates(self, chain_key: str, graph_limit: int, *, include_gecko: bool = True) -> tuple[list[dict[str,Any]],list[dict[str,Any]]]:
        graph=self.discovery.graph_sample(chain_key,graph_limit)
        dex=self.discovery.dexscreener_sample(chain_key)
        # Manual Candidate Universe builds still include one Gecko page. Bulk
        # Advisor refreshes can deliberately skip it: Gecko is the scarce
        # provider and must not make a six-chain comparison wait on cooldowns.
        if include_gecko:
            gecko=self.discovery.gecko_sample(chain_key,1)
        else:
            gecko={
                "provider":"GECKOTERMINAL","status":"SKIPPED_CONSUMER_PROFILE",
                "requests":0,"candidates":[],"candidate_count":0,"elapsed_ms":0,
                "error":"Skipped for fast shared-consumer refresh; live validation uses DEX Screener.",
            }
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

    def _profit_lab_readiness(self, row: dict[str,Any], chain_key: str) -> dict[str,Any]:
        """Describe whether deep execution-price history is already usable.

        This is intentionally separate from research_ready: a pool can have live
        TVL/volume economics yet still need pair-ratio history before Profit Lab
        can optimise a volatile/volatile range.
        """
        chain=str(chain_key or row.get("chain") or "").upper()
        address=_addr(row.get("pool_address"))
        validation=str(row.get("economic_validation") or "").upper()
        if not address or validation not in {"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"}:
            return {
                "ready":False,"status":"MARKET_VALIDATION_REQUIRED",
                "label":"VALIDATION NEEDED",
                "reason":"Live pool economics must be validated before deep range analysis.",
            }

        cache_key=f"profit:history:v09642:{chain}:{address}:hour"
        try:
            payload=self.store.get_setting(cache_key,{}) or {}
        except Exception:
            payload={}
        candles=list(payload.get("candles") or [])
        latest=max((_f(x.get("timestamp")) for x in candles),default=0.0)
        age=max(0.0,time.time()-latest) if latest>0 else None
        if len(candles)>=24 and age is not None and age<=8*3600:
            return {
                "ready":True,"status":"READY_CACHED_HISTORY","label":"PROFIT READY",
                "reason":"Validated execution-price history is already cached.",
                "history_samples":len(candles),"history_age_seconds":round(age,1),
                "history_provider":payload.get("provider"),
            }

        status_key=f"profit:evidence:status:{chain}:{address}"
        try:
            warm=self.store.get_setting(status_key,{}) or {}
        except Exception:
            warm={}
        checked=_f(warm.get("checked_at") or warm.get("last_run_at"))
        warm_age=max(0.0,time.time()-checked) if checked>0 else None
        warm_samples=int(_f(warm.get("samples")))
        if warm.get("ok") and warm_samples>=24 and (
            bool(warm.get("fresh")) or (warm_age is not None and warm_age<=8*3600)
        ):
            return {
                "ready":True,"status":"READY_WARMED_HISTORY","label":"PROFIT READY",
                "reason":"Background evidence warming has prepared deep pair history.",
                "history_samples":warm_samples,"history_age_seconds":round(warm_age,1) if warm_age is not None else None,
                "history_provider":warm.get("provider"),
            }
        if warm.get("status")=="FAILED" and warm_age is not None and warm_age<=6*3600:
            reason=str(warm.get("error") or "Deep pair history could not be prepared.")
            return {
                "ready":False,"status":"HISTORY_FAILED","label":"HISTORY FAILED",
                "reason":reason[:140],"history_samples":warm_samples,
                "last_checked_age_seconds":round(warm_age,1),
            }

        symbols=_symbols(row)
        if symbols & _PROFIT_STABLE_SYMBOLS:
            return {
                "ready":True,"status":"DIRECT_HISTORY_PATH","label":"DIRECT PATH",
                "reason":"Stable-quoted pool has a direct USD history path; deep history is fetched or warmed on demand.",
            }
        return {
            "ready":False,"status":"PAIR_HISTORY_REQUIRED","label":"HISTORY NEEDED",
            "reason":"Non-stable pair needs validated pair-ratio history before Profit Lab can optimise it.",
        }

    def _attach_profit_readiness(self, rows: list[dict[str,Any]], chain_key: str) -> list[dict[str,Any]]:
        out=[]
        for original in rows:
            row=dict(original)
            row["profit_lab_readiness"]=self._profit_lab_readiness(row,chain_key)
            out.append(row)
        return out

    def refresh(self, chain_key: str, *, graph_limit: int = 500, shortlist_limit: int = 40, validate_limit: int = 20, include_gecko: bool = True, preserve_existing_on_failure: bool = False) -> dict[str,Any]:
        chain_key=str(chain_key or "").upper()
        if chain_key not in CHAINS:
            raise ValueError(f"Unsupported chain: {chain_key}")
        prior=self.cached(chain_key) if preserve_existing_on_failure else None
        started=time.perf_counter()
        rows,providers=self._broad_candidates(chain_key,max(50,min(1000,int(graph_limit))),include_gecko=include_gecko)
        ctx=self._portfolio_context(chain_key)
        shortlist,filtered=self._cheap_filter(rows,ctx,shortlist_limit)
        targeted_requests,target_errors=self._targeted_validate(chain_key,shortlist,validate_limit)

        for row in shortlist:
            row["research_ready"]=self._research_ready(row)
            row["profit_lab_readiness"]=self._profit_lab_readiness(row,chain_key)
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
        profit_ready=sum(1 for x in shortlist if (x.get("profit_lab_readiness") or {}).get("ready"))
        profit_history_needed=sum(
            1 for x in shortlist
            if str((x.get("profit_lab_readiness") or {}).get("status") or "")=="PAIR_HISTORY_REQUIRED"
        )
        profit_history_failed=sum(
            1 for x in shortlist
            if str((x.get("profit_lab_readiness") or {}).get("status") or "")=="HISTORY_FAILED"
        )
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
            "mode":"SHARED_CANDIDATE_UNIVERSE",
            "feeds_strategy":True,
            "feeds_scout":True,
            "feeds_portfolio_advisor":True,
            "chain":chain_key,
            "generated_at":time.time(),
            "graph_limit":int(graph_limit),
            "shortlist_limit":int(shortlist_limit),
            "validate_limit":int(validate_limit),
            "include_gecko":bool(include_gecko),
            "summary":{
                "v3_discovered":len(rows),
                "cheap_filtered_out":filtered,
                "shortlisted":len(shortlist),
                "live_validated":live_validated,
                "awaiting_live_validation":awaiting,
                "research_ready":ready,
                "profit_lab_ready":profit_ready,
                "profit_history_needed":profit_history_needed,
                "profit_history_failed":profit_history_failed,
                "portfolio_overlaps":overlaps,
                "version_conflicts":conflicts,
                "targeted_live_requests":targeted_requests,
                "elapsed_ms":round((time.perf_counter()-started)*1000,1),
            },
            "providers":provider_summary,
            "shortlist":shortlist,
            "targeted_validation_errors":target_errors[:5],
            "note":"Discovery score only controls the cheap shortlist. Scout and Portfolio Advisor apply their own risk/economics ranking to this shared universe; Profit Lab remains on-demand.",
        }
        if preserve_existing_on_failure and not snapshot.get("ok") and isinstance(prior,dict) and prior.get("ok"):
            return {
                **prior,
                "background_refresh_preserved":True,
                "background_refresh_error":"No usable candidate rows were returned; preserved the previous valid universe snapshot.",
                "refresh_attempted_at":time.time(),
                "providers":provider_summary,
            }
        try:
            self.store.set_setting(f"candidate_universe:{chain_key}",snapshot)
        except Exception:
            pass
        return snapshot

    def fresh_cached(self, chain_key: str, *, max_age_seconds: float = 180.0) -> dict[str,Any] | None:
        """Return a recent persisted universe without spending provider requests."""
        payload=self.cached(chain_key)
        generated=float(payload.get("generated_at") or 0)
        age=max(0.0,time.time()-generated) if generated>0 else float("inf")
        if payload.get("ok") and generated>0 and age <= max(1.0,float(max_age_seconds)):
            return {**payload,"cached":True,"fresh_cache":True,"cache_age_seconds":round(age,1)}
        return None

    def cached(self, chain_key: str) -> dict[str,Any]:
        chain_key=str(chain_key or "").upper()
        if chain_key not in CHAINS:
            raise ValueError(f"Unsupported chain: {chain_key}")
        try:
            payload=self.store.get_setting(f"candidate_universe:{chain_key}",None)
        except Exception:
            payload=None
        if isinstance(payload,dict) and payload:
            out={**payload,"cached":True}
            rows=self._attach_profit_readiness(list(out.get("shortlist") or []),chain_key)
            out["shortlist"]=rows
            summary=dict(out.get("summary") or {})
            summary["profit_lab_ready"]=sum(1 for x in rows if (x.get("profit_lab_readiness") or {}).get("ready"))
            summary["profit_history_needed"]=sum(
                1 for x in rows
                if str((x.get("profit_lab_readiness") or {}).get("status") or "")=="PAIR_HISTORY_REQUIRED"
            )
            summary["profit_history_failed"]=sum(
                1 for x in rows
                if str((x.get("profit_lab_readiness") or {}).get("status") or "")=="HISTORY_FAILED"
            )
            out["summary"]=summary
            return out
        return {
            "ok":False,
            "cached":True,
            "mode":"SHARED_CANDIDATE_UNIVERSE",
            "feeds_strategy":True,
            "feeds_scout":True,
            "feeds_portfolio_advisor":True,
            "chain":chain_key,
            "summary":{},
            "providers":[],
            "shortlist":[],
            "note":"No shared candidate universe has been built for this chain yet.",
        }
