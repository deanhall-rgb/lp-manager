from __future__ import annotations
import time
from typing import Any
from .chain_registry import CHAINS

DEFAULT_CHAINS=("ETHEREUM","BASE","ARBITRUM","OPTIMISM","POLYGON","ROBINHOOD_CHAIN")
VALID={"CROSS_VALIDATED","LIVE_VALIDATED","TVL_MISMATCH"}

def _f(v,d=0.0):
    try:return float(d if v is None else v)
    except (TypeError,ValueError):return float(d)

def _fresh(age):
    if age is None:return {"state":"UNKNOWN","label":"UNKNOWN","penalty":30.0}
    age=max(0.0,float(age))
    if age<=180:return {"state":"FRESH","label":"FRESH","penalty":0.0}
    if age<=900:return {"state":"RECENT","label":"RECENT","penalty":3.0}
    if age<=3600:return {"state":"AGING","label":"AGING","penalty":min(12.0,3.0+(age-900)/300)}
    return {"state":"STALE","label":"STALE","penalty":min(30.0,12.0+(age-3600)/400)}

def _version(row):
    p=str(row.get("protocol") or row.get("protocol_guess") or "").upper()
    v=str(row.get("version") or "").upper()
    if "V4" in p or v=="V4":return "V4"
    if "V3" in p or v=="V3":return "V3"
    return v or "UNKNOWN"

class OpportunityLeaderboard:
    STORE_KEY="opportunity_leaderboard:v0973"
    def __init__(self,store,candidate_universe):
        self.store=store;self.universe=candidate_universe

    def _deep_index(self):
        out={}
        try: rows=self.store.list_forecast_snapshots(500)
        except Exception: rows=[]
        for r in rows:
            key=(str(r.get("chain") or "").upper(),str(r.get("pool_address") or "").lower())
            if not key[0] or not key[1] or key in out:continue
            payload=dict(r.get("payload") or {});best=dict(payload.get("recommended_range") or {})
            forecast=dict(best.get("forecast") or {})
            out[key]={
                "created_at":_f(r.get("created_at")),"horizon_days":_f(r.get("horizon_days")),
                "capital_usd":_f(r.get("capital_usd")),"expected_net_usd":_f(r.get("expected_net_usd")),
                "expected_fees_usd":_f(r.get("expected_fees_usd")),"forecast_fee_apr_pct":_f(r.get("forecast_fee_apr_pct")),
                "range_quality_score":_f(best.get("range_quality_score",best.get("profit_score"))),
                "net_horizon_return_pct":_f(forecast.get("net_horizon_return_pct",forecast.get("expected_net_pct"))),
                "sleeve":str(r.get("sleeve") or ""),"forecast_id":str(r.get("id") or "")
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
        age=max(0.0,now-_f(deep.get("created_at")));fresh=age<=21600;net=_f(deep.get("expected_net_usd"))
        if not fresh:status,label="DEEP_STALE","DEEP STALE"
        elif net>0:status,label="DEEP_PROFITABLE","DEEP +VE"
        else:status,label="DEEP_NON_POSITIVE","DEEP -VE"
        return {**deep,"status":status,"label":label,"fresh":fresh,"age_seconds":round(age,1),"allocation_confirmed":bool(fresh and net>0)}

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
            chain_state.append({"chain":chain,"has_snapshot":bool(snap.get("ok") or short),"generated_at":generated or None,"age_seconds":round(age,1) if age is not None else None,"freshness":fr["state"],"shortlisted":len(short)})
            for raw in short:
                pool=str(raw.get("pool_address") or "").lower()
                if not pool:continue
                readiness=dict(raw.get("profit_lab_readiness") or {});validation=str(raw.get("economic_validation") or "").upper()
                ds=self._deep_state(deep.get((chain,pool)),now);pi=econ.get((chain,pool),{})
                eligible=bool(raw.get("research_ready") and validation in VALID and fr["state"]!="STALE")
                rows.append({
                    "pair":str(raw.get("pair") or "?"),"chain":chain,"pool_address":raw.get("pool_address"),
                    "protocol":str(raw.get("protocol") or raw.get("protocol_guess") or "UNISWAP_V3"),"protocol_version":_version(raw),
                    "tvl_usd":_f(raw.get("tvl_usd")),"volume_24h_usd":_f(raw.get("volume_24h_usd")),
                    "fee_apr_proxy":_f(raw.get("gross_fee_apr_proxy")),"discovery_score":_f(raw.get("discovery_score")),
                    "screen_score":self._screen_score(raw,fr),"score_type":"PROVISIONAL_SCREEN_SCORE",
                    "economic_validation":validation or "UNVERIFIED","profit_lab_readiness":readiness,
                    "freshness":fr,"snapshot_age_seconds":round(age,1) if age is not None else None,
                    "portfolio_overlap":bool(raw.get("portfolio_overlap")),"portfolio_exposure":_f(raw.get("portfolio_exposure")),
                    "quick_economics":dict(pi.get("quick_economics") or raw.get("quick_economics") or {}),
                    "preferred_sleeve":pi.get("preferred_sleeve") or "","deep_analysis":ds,
                    "leaderboard_eligible":eligible,"allocation_confirmed":bool(eligible and readiness.get("ready") and ds.get("allocation_confirmed"))
                })
        dedup={}
        for r in rows:
            key=(r["chain"],str(r.get("pool_address") or "").lower())
            old=dedup.get(key)
            if old is None or (r["leaderboard_eligible"],r["screen_score"],r["volume_24h_usd"])>(old["leaderboard_eligible"],old["screen_score"],old["volume_24h_usd"]):dedup[key]=r
        all_rows=list(dedup.values())
        all_rows.sort(key=lambda r:(r["leaderboard_eligible"],bool((r["deep_analysis"] or {}).get("fresh")),bool((r["profit_lab_readiness"] or {}).get("ready")),r["screen_score"],r["volume_24h_usd"],r["tvl_usd"]),reverse=True)
        eligible=[r for r in all_rows if r["leaderboard_eligible"]];top=eligible[:max(1,min(100,int(limit)))]
        for i,r in enumerate(top,1):r["rank"]=i
        funnel={**tot,"chains_requested":len(chains),"chains_with_snapshots":sum(1 for x in chain_state if x["has_snapshot"]),"persistent_candidates":len(all_rows),"leaderboard_eligible":len(eligible),"shown":len(top),"deep_analysed":sum(1 for r in eligible if (r["deep_analysis"] or {}).get("status")!="NOT_ANALYSED"),"allocation_confirmed":sum(1 for r in eligible if r["allocation_confirmed"])}
        payload={"ok":True,"mode":"PERSISTENT_CROSS_CHAIN_LEADERBOARD_FOUNDATION","generated_at":now,"score_version":"SCREEN_FOUNDATION_V0973","score_is_final_opportunity_score":False,"chains":chain_state,"funnel":funnel,"rows":top,"note":"Persistent cross-chain leaderboard foundation. Screen score is provisional; v0.9.7.4 adds the final transparent Opportunity Score."}
        try:self.store.set_setting(self.STORE_KEY,payload)
        except Exception:pass
        return payload

    def cached(self):
        try:p=self.store.get_setting(self.STORE_KEY,None)
        except Exception:p=None
        return p if isinstance(p,dict) and p else self.rebuild()
