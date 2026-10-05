from __future__ import annotations
import time
from typing import Any
from .chain_registry import CHAINS
from .opportunity_score import SCORE_VERSION, score_opportunity
from .product import ECONOMICS_MODEL_VERSION
from .asset_registry import STABLE_SYMBOLS, ETH_SYMBOLS, BTC_SYMBOLS

DEFAULT_CHAINS=("ETHEREUM","BASE","ARBITRUM","OPTIMISM","POLYGON","ROBINHOOD_CHAIN")
VALID={"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"}

def _f(v,d=0.0):
    try:return float(d if v is None else v)
    except (TypeError,ValueError):return float(d)

def _fresh(age):
    if age is None:return {"state":"UNKNOWN","label":"UNKNOWN","penalty":30.0}
    age=max(0.0,float(age))
    # Candidate Universe is now maintained as a background cross-chain service.
    # A 3-minute expiry made a healthy board turn yellow almost immediately and
    # eventually disappear despite still-valid persisted candidates.
    if age<=900:return {"state":"FRESH","label":"FRESH","penalty":0.0}
    if age<=1800:return {"state":"RECENT","label":"RECENT","penalty":3.0}
    if age<=7200:return {"state":"AGING","label":"AGING","penalty":min(14.0,3.0+(age-1800)/600)}
    return {"state":"STALE","label":"STALE","penalty":min(30.0,14.0+(age-7200)/900)}

def _version(row):
    p=str(row.get("protocol") or row.get("protocol_guess") or "").upper()
    v=str(row.get("version") or "").upper()
    if "V4" in p or v=="V4":return "V4"
    if "V3" in p or v=="V3":return "V3"
    return v or "UNKNOWN"

def _economic_symbol(symbol):
    value=str(symbol or "").strip().upper()
    if value in STABLE_SYMBOLS:return "USD"
    if value in ETH_SYMBOLS:return "ETH"
    if value in BTC_SYMBOLS:return "BTC"
    if value in {"POL","WPOL","MATIC","WMATIC"}:return "POL"
    if value in {"BNB","WBNB"}:return "BNB"
    if value in {"AVAX","WAVAX"}:return "AVAX"
    if value in {"SOL","WSOL"}:return "SOL"
    return value

def _pair_family(pair):
    parts=[_economic_symbol(x) for x in str(pair or "").replace("-","/").split("/") if str(x).strip()]
    if len(parts)<2:return ""
    return "/".join(sorted(parts[:2]))

class OpportunityLeaderboard:
    STORE_KEY="opportunity_leaderboard:v0974"
    def __init__(self,store,candidate_universe):
        self.store=store;self.universe=candidate_universe

    def _deep_index(self):
        out={}
        priority={}
        try: rows=self.store.list_forecast_snapshots(500)
        except Exception: rows=[]
        for r in rows:
            key=(str(r.get("chain") or "").upper(),str(r.get("pool_address") or "").lower())
            if not key[0] or not key[1]:continue
            payload=dict(r.get("payload") or {});best=dict(payload.get("recommended_range") or {})
            forecast=dict(best.get("forecast") or {})
            basis=dict(payload.get("leaderboard_deep_basis") or {})
            economics_model=str(payload.get("economics_model_version") or forecast.get("economics_model_version") or "")
            # Once v0.9.7.5 has produced a standard 7-day / display-£1k
            # leaderboard snapshot, later ad-hoc 1d/3d Profit Lab investigations
            # must not silently replace the cross-pool comparison basis.
            standard_basis=bool(basis and str(payload.get("analysis_source") or "")=="BACKGROUND_LEADERBOARD_V0975")
            pref=(
                2 if standard_basis and economics_model==ECONOMICS_MODEL_VERSION else
                1 if economics_model==ECONOMICS_MODEL_VERSION else
                0,
                _f(r.get("created_at")),
            )
            if key in out and pref<=priority.get(key,(-1,0.0)):continue
            priority[key]=pref
            out[key]={
                "created_at":_f(r.get("created_at")),"horizon_days":_f(r.get("horizon_days")),
                "capital_usd":_f(r.get("capital_usd")),"expected_net_usd":_f(r.get("expected_net_usd")),
                "expected_fees_usd":_f(r.get("expected_fees_usd")),"forecast_fee_apr_pct":_f(r.get("forecast_fee_apr_pct")),
                "pool_apr_24h_annualised_pct":forecast.get("pool_apr_24h_annualised_pct"),
                "pool_apr_evidence_class":str((forecast.get("pool_apr_24h_evidence") or {}).get("evidence_class") or ""),
                "pool_apr_is_observed":bool((forecast.get("pool_apr_24h_evidence") or {}).get("observed")),
                "modelled_position_apr_pct":forecast.get("modelled_position_apr_pct",r.get("forecast_fee_apr_pct")),
                "economics_model_version":economics_model,
                "economics_evidence_class":str(forecast.get("position_economics_evidence_class") or "LEGACY_DEEP_FORECAST"),
                "economics_confidence":str(forecast.get("position_economics_confidence") or ""),
                "position_to_pool_apr_uplift_ratio":forecast.get("position_to_pool_apr_uplift_ratio"),
                "expected_intervention_cost_usd":_f(forecast.get("expected_intervention_cost_usd")),
                "range_quality_score":_f(best.get("range_quality_score",best.get("profit_score"))),
                "net_horizon_return_pct":_f(forecast.get("net_horizon_return_pct",forecast.get("expected_net_pct"))),
                "sleeve":str(r.get("sleeve") or ""),"forecast_id":str(r.get("id") or ""),
                "analysis_source":str(payload.get("analysis_source") or "MANUAL_PROFIT_LAB"),
                "leaderboard_deep_basis":basis,
                "standard_basis":standard_basis,
            }
        return out

    def _econ_index(self):
        out={}
        try:ops=self.store.list_opportunities(1000)
        except Exception:ops=[]
        for op in ops:
            c=dict(op.get("candidate") or {});e=dict(op.get("evaluation") or {})
            key=(str(c.get("chain") or op.get("chain") or "").upper(),str(c.get("pool_address") or op.get("pool_address") or "").lower())
            if not key[0] or not key[1]:continue
            out[key]={
                "quick_economics":dict(c.get("quick_economics") or e.get("advisor_economics") or e.get("quick_economics") or {}),
                "preferred_sleeve":str(c.get("sleeve") or e.get("advisor_sleeve") or e.get("preferred_sleeve") or "")
            }
        return out

    @staticmethod
    def _deep_state(deep,now):
        if not deep:return {"status":"NOT_ANALYSED","label":"SCREEN ONLY","fresh":False,"allocation_confirmed":False}
        row=dict(deep)
        payload=dict(row.get("payload") or {})
        best=dict(payload.get("recommended_range") or {})
        forecast=dict(best.get("forecast") or {})
        if "expected_intervention_cost_usd" not in row:
            row["expected_intervention_cost_usd"]=_f(forecast.get("expected_intervention_cost_usd"))
        if "expected_fees_usd" not in row or row.get("expected_fees_usd") is None:
            row["expected_fees_usd"]=_f(forecast.get("expected_fees_usd"))
        if "expected_net_usd" not in row or row.get("expected_net_usd") is None:
            row["expected_net_usd"]=_f(forecast.get("expected_net_usd"))
        age=max(0.0,now-_f(row.get("created_at")));time_fresh=age<=21600;net=_f(row.get("expected_net_usd"))
        economics_current=str(row.get("economics_model_version") or "")==ECONOMICS_MODEL_VERSION
        if not economics_current:
            status,label="DEEP_RECHECK_REQUIRED","DEEP RECHECK"
        elif not time_fresh:
            status,label="DEEP_STALE","DEEP STALE"
        elif net>0:
            status,label="DEEP_PROFITABLE","DEEP +VE"
        else:
            status,label="DEEP_NON_POSITIVE","DEEP -VE"
        fresh=bool(time_fresh and economics_current)
        return {
            **row,
            "status":status,"label":label,"fresh":fresh,
            "time_fresh":time_fresh,
            "economics_model_current":economics_current,
            "required_economics_model_version":ECONOMICS_MODEL_VERSION,
            "age_seconds":round(age,1),
            "allocation_confirmed":bool(fresh and net>0),
        }

    @staticmethod
    def _screen_score(row,freshness):
        score=_f(row.get("discovery_score"))
        if str(row.get("economic_validation") or "").upper() in VALID:score+=5
        if row.get("research_ready"):score+=4
        if (row.get("profit_lab_readiness") or {}).get("ready"):score+=4
        score-=_f(freshness.get("penalty"))
        return round(max(0,min(100,score)),1)

    def rebuild(self,chains=None,limit=25):
        now=time.time();chains=tuple(dict.fromkeys(str(x).upper() for x in (chains or DEFAULT_CHAINS)))
        chains=tuple(x for x in chains if x in CHAINS);deep=self._deep_index();econ=self._econ_index()
        rows=[];chain_state=[];tot={"v3_considered":0,"shortlisted":0,"live_validated":0,"research_ready":0,"profit_ready":0}
        for chain in chains:
            snap=self.universe.cached(chain);summary=dict(snap.get("summary") or {});generated=_f(snap.get("generated_at"))
            age=max(0,now-generated) if generated else None;fr=_fresh(age);short=list(snap.get("shortlist") or [])
            for k,src in (("v3_considered","v3_discovered"),("shortlisted","shortlisted"),("live_validated","live_validated"),("research_ready","research_ready"),("profit_ready","profit_lab_ready")):
                tot[k]+=int(_f(summary.get(src)))
            chain_state.append({
                "chain":chain,"has_snapshot":bool(snap.get("ok") or short),
                "generated_at":generated or None,"age_seconds":round(age,1) if age is not None else None,
                "freshness":fr["state"],"shortlisted":len(short),
                "retained_in_shortlist":sum(1 for x in short if x.get("universe_retained")),
            })
            for raw in short:
                pool=str(raw.get("pool_address") or "").lower()
                if not pool:continue
                readiness=dict(raw.get("profit_lab_readiness") or {});validation=str(raw.get("economic_validation") or "").upper()
                ds=self._deep_state(deep.get((chain,pool)),now);pi=econ.get((chain,pool),{})
                retained=bool(raw.get("universe_retained"))
                last_seen=_f(raw.get("universe_last_seen_at"))
                row_age=max(0.0,now-last_seen) if last_seen>0 else age
                row_fr=_fresh(row_age)
                # A pool may remain visible across provider sampling jitter, but
                # retained evidence cannot confirm an allocation until an exact
                # live lookup or broad discovery sees it again.
                eligible=bool(raw.get("research_ready") and validation in VALID)
                market_fresh_for_allocation=bool(
                    not retained and row_fr["state"] in {"FRESH","RECENT"}
                )
                rows.append({
                    "pair":str(raw.get("pair") or "?"),"chain":chain,"pool_address":raw.get("pool_address"),
                    "protocol":str(raw.get("protocol") or raw.get("protocol_guess") or "UNISWAP_V3"),"protocol_version":_version(raw),
                    "tvl_usd":_f(raw.get("tvl_usd")),"volume_24h_usd":_f(raw.get("volume_24h_usd")),
                    "fee_apr_proxy":_f(raw.get("gross_fee_apr_proxy"),_f(ds.get("pool_apr_24h_annualised_pct"))),
                    "fee_apr_proxy_evidence_class":str(raw.get("fee_apr_proxy_evidence_class") or ds.get("pool_apr_evidence_class") or ""),
                    "fee_apr_proxy_observed":bool(raw.get("fee_apr_proxy_observed") or ds.get("pool_apr_is_observed")),
                    "turnover_24h":_f(raw.get("turnover_24h"),_f(raw.get("volume_24h_usd"))/max(_f(raw.get("tvl_usd")),1.0)),"discovery_score":_f(raw.get("discovery_score")),
                    "screen_score":self._screen_score(raw,row_fr),"score_type":"PROVISIONAL_SCREEN_SCORE",
                    "economic_validation":validation or "UNVERIFIED","profit_lab_readiness":readiness,
                    "freshness":row_fr,"snapshot_age_seconds":round(age,1) if age is not None else None,
                    "market_evidence_age_seconds":round(row_age,1) if row_age is not None else None,
                    "universe_retained":retained,
                    "universe_missed_refreshes":int(_f(raw.get("universe_missed_refreshes"))),
                    "universe_last_seen_at":last_seen or None,
                    "universe_origin":str(raw.get("universe_origin") or ""),
                    "portfolio_overlap":bool(raw.get("portfolio_overlap")),"portfolio_exposure":_f(raw.get("portfolio_exposure")),
                    "quick_economics":dict(pi.get("quick_economics") or raw.get("quick_economics") or {}),
                    "preferred_sleeve":pi.get("preferred_sleeve") or "","deep_analysis":ds,
                    "leaderboard_eligible":eligible,
                    "market_fresh_for_allocation":market_fresh_for_allocation,
                    "allocation_confirmed":bool(eligible and market_fresh_for_allocation and readiness.get("ready") and ds.get("allocation_confirmed"))
                })
        dedup={}
        for r in rows:
            key=(r["chain"],str(r.get("pool_address") or "").lower())
            old=dedup.get(key)
            if old is None or (r["leaderboard_eligible"],r["screen_score"],r["volume_24h_usd"])>(old["leaderboard_eligible"],old["screen_score"],old["volume_24h_usd"]):dedup[key]=r
        all_rows=list(dedup.values())
        for r in all_rows:
            r["opportunity_score"]=score_opportunity(r)
            r["score"]=r["opportunity_score"]["score"]
        all_rows.sort(key=lambda r:(r["leaderboard_eligible"],_f((r.get("opportunity_score") or {}).get("score")),bool((r["deep_analysis"] or {}).get("fresh")),bool((r["profit_lab_readiness"] or {}).get("ready")),r["volume_24h_usd"],r["tvl_usd"]),reverse=True)
        eligible=[r for r in all_rows if r["leaderboard_eligible"]]

        # One economic trade family gets one Top-25 seat. USDC/WETH, WETH/USDT
        # and their cross-chain equivalents compete against each other and only
        # the strongest representative survives. This stops safe giant pairs
        # consuming most of the board through wrappers/stable denominations.
        family_counts={}
        for r in eligible:
            family=_pair_family(r.get("pair")) or f"{r['chain']}:{str(r.get('pool_address') or '').lower()}"
            r["pair_family"]=family
            family_counts[family]=family_counts.get(family,0)+1
        unique=[]
        seen=set()
        for r in eligible:
            family=r.get("pair_family")
            if family in seen:continue
            seen.add(family)
            row=dict(r)
            row["equivalent_alternatives_hidden"]=max(0,int(family_counts.get(family,1))-1)
            unique.append(row)
        top=unique[:max(1,min(100,int(limit)))]
        for i,r in enumerate(top,1):r["rank"]=i
        alternatives_hidden=max(0,len(eligible)-len(unique))
        funnel={
            **tot,
            "chains_requested":len(chains),
            "chains_with_snapshots":sum(1 for x in chain_state if x["has_snapshot"]),
            "persistent_candidates":len(all_rows),
            "retained_candidates":sum(1 for r in all_rows if r.get("universe_retained")),
            "leaderboard_eligible":len(eligible),
            "unique_pair_families":len(unique),
            "equivalent_alternatives_hidden":alternatives_hidden,
            "shown":len(top),
            "deep_analysed":sum(1 for r in unique if (r["deep_analysis"] or {}).get("status")!="NOT_ANALYSED"),
            "allocation_confirmed":sum(1 for r in unique if r["allocation_confirmed"]),
        }
        payload={"ok":True,"mode":"PERSISTENT_CROSS_CHAIN_OPPORTUNITY_SCORE","generated_at":now,"score_version":SCORE_VERSION,"score_is_final_opportunity_score":True,"chains":chain_state,"funnel":funnel,"rows":top,"note":"Profit-first Opportunity Score ranks expected net economics first, then durability, liquidity, sustainable activity, risk/friction and evidence quality. Equivalent wrapped/stable/cross-chain pair families share one leaderboard seat. Provider-sample misses are retained briefly but cannot confirm allocation until live-revalidated."}
        try:self.store.set_setting(self.STORE_KEY,payload)
        except Exception:pass
        return payload

    def attach_analysis_state(self, rows):
        """Attach strategy-horizon-matched deep evidence for Portfolio Advisor.

        Tactical screening is confirmed against a 3-day Profit Lab run; Core is
        confirmed against 30 days. This prevents a profitable 1-day plan from
        silently validating a different 7/30-day decision context.
        """
        now=time.time(); out=[]
        try: forecasts=self.store.list_forecast_snapshots(500)
        except Exception: forecasts=[]
        for item in rows:
            row=dict(item)
            chain=str(row.get("chain") or "").upper()
            pool=str(row.get("pool_address") or "").lower()
            evaluation=row.get("evaluation") or {}
            sleeve=str(row.get("sleeve") or evaluation.get("preferred_sleeve") or "").upper()
            if not sleeve:
                sleeve="CORE_INCOME" if _f(evaluation.get("core_pre_score"))>=_f(evaluation.get("tactical_pre_score")) else "TACTICAL_CAMPAIGN"
            required=30.0 if sleeve=="CORE_INCOME" else 3.0
            match=None
            for candidate in forecasts:
                if str(candidate.get("chain") or "").upper()!=chain: continue
                if str(candidate.get("pool_address") or "").lower()!=pool: continue
                if abs(_f(candidate.get("horizon_days"))-required)>0.01: continue
                match=candidate
                break
            deep=self._deep_state(match,now)
            deep["required_horizon_days"]=required
            deep["horizon_match"]=bool(match)
            deep["advisor_sleeve"]=sleeve
            row["deep_analysis"]=deep
            out.append(row)
        return out

    def cached(self):
        try:p=self.store.get_setting(self.STORE_KEY,None)
        except Exception:p=None
        return p if isinstance(p,dict) and p else self.rebuild()
