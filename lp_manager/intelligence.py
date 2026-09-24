from __future__ import annotations

import json
import time
import os
from datetime import datetime, timezone
from typing import Any

import requests

from .analytics import portfolio_summary, enrich_position
from .strategy import deterministic_plan, edge_risk


def _extract_response_text(payload: dict[str, Any]) -> str:
    chunks=[]
    for item in payload.get("output") or []:
        if item.get("type") != "message": continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
    return "\n".join(chunks).strip()


class IntelligenceService:
    def __init__(self, settings, store):
        self.settings=settings; self.store=store

    def _usage_status(self) -> dict[str, Any]:
        usage=self.store.get_setting("ai:usage",{}) or {}
        day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today=(usage.get("by_day") or {}).get(day,{})
        return {"total":{k:usage.get(k,0) for k in ("calls","input_tokens","output_tokens","estimated_cost_usd")},"today":today,"by_purpose":usage.get("by_purpose") or {},"cost_note":"Token cost estimate uses current configured/model defaults and excludes any separately billed web-search tool fees."}

    def status(self) -> dict[str, Any]:
        return {"enabled":bool(self.settings.ai_enabled),"configured":bool(self.settings.openai_api_key),"model":self.settings.openai_model,"web_search":bool(self.settings.ai_web_search),"authority":"ADVISORY_ONLY","usage":self._usage_status(),"policy":"DETERMINISTIC_ALWAYS; AI_ON_USER_REQUEST_OR_MATERIAL_EVENT; DEDUPLICATED_BACKGROUND_EVENTS"}

    def _record_usage(self, payload: dict[str,Any], purpose: str) -> None:
        row=payload.get("usage") or {}; inp=int(row.get("input_tokens") or 0); out=int(row.get("output_tokens") or 0)
        model=str(payload.get("model") or self.settings.openai_model)
        defaults=(2.0,12.0) if "gpt-5.6-terra" in model else (0.0,0.0)
        in_rate=float(os.getenv("OPENAI_INPUT_USD_PER_1M",str(defaults[0])) or 0); out_rate=float(os.getenv("OPENAI_OUTPUT_USD_PER_1M",str(defaults[1])) or 0)
        cost=(inp*in_rate+out*out_rate)/1_000_000.0
        usage=self.store.get_setting("ai:usage",{}) or {}; day=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for k,v in (("calls",1),("input_tokens",inp),("output_tokens",out),("estimated_cost_usd",cost)): usage[k]=float(usage.get(k,0) or 0)+v
        by_day=usage.setdefault("by_day",{}); d=by_day.setdefault(day,{"calls":0,"input_tokens":0,"output_tokens":0,"estimated_cost_usd":0.0})
        d["calls"]+=1; d["input_tokens"]+=inp; d["output_tokens"]+=out; d["estimated_cost_usd"]+=cost
        purposes=usage.setdefault("by_purpose",{}); q=purposes.setdefault(purpose,{"calls":0,"estimated_cost_usd":0.0}); q["calls"]+=1; q["estimated_cost_usd"]+=cost
        # bound daily history
        for old in sorted(by_day)[:-31]: by_day.pop(old,None)
        self.store.set_setting("ai:usage",usage)

    def _ask(self, task: str, context: dict[str, Any], fallback: dict[str, Any], *, purpose: str = "ADVISORY") -> dict[str, Any]:
        if not self.settings.ai_enabled or not self.settings.openai_api_key:
            return {**fallback,"ai_mode":"DETERMINISTIC_FALLBACK","model":None,"generated_at":time.time()}
        instructions=(
            "You are the advisory intelligence layer for an LP portfolio manager. Never claim execution occurred. "
            "The primary objective is sustainable LP profit: keep productive capital earning fees while controlling blow-up, scam, concentration and avoidable execution risk. "
            "Capital preservation is a guardrail, not a default instruction to stay in cash. Distinguish CORE_INCOME from TACTICAL_CAMPAIGN. "
            "Optimise sustainable expected fee operating return subject to explicit risk constraints; do not confuse high headline APR with profit. "
            "Bull, bear and choppy regimes can all support LP positions when the asset thesis, range and inventory outcome are appropriate. "
            "When web research is available, use it to assess current asset/protocol sentiment and distinguish it from technical price momentum. "
            "Return ONLY a compact JSON object with keys: headline, status, confidence, recommendation, market_sentiment, profit_case, reasons, risks, counterargument, invalidation, next_review. "
            "confidence is 0-100. reasons and risks are arrays of short strings. If sentiment or economics evidence is missing, say so instead of inventing it."
        )
        schema = {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "status": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 100},
                "recommendation": {"type": "string"},
                "market_sentiment": {"type": "string"},
                "profit_case": {"type": "string"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "counterargument": {"type": "string"},
                "invalidation": {"type": "string"},
                "next_review": {"type": "string"},
            },
            "required": ["headline", "status", "confidence", "recommendation", "market_sentiment", "profit_case", "reasons", "risks", "counterargument", "invalidation", "next_review"],
            "additionalProperties": False,
        }
        body={
            "model":self.settings.openai_model,
            "instructions":instructions,
            "input":f"TASK: {task}\nDATA:\n{json.dumps(context,sort_keys=True,default=str)}",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "lp_manager_advisory",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if self.settings.ai_web_search: body["tools"]=[{"type":"web_search"}]
        try:
            r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":f"Bearer {self.settings.openai_api_key}","Content-Type":"application/json"},json=body,timeout=40)
            r.raise_for_status(); payload=r.json(); self._record_usage(payload,purpose); text=_extract_response_text(payload)
            parsed=json.loads(text)
            if not isinstance(parsed,dict): raise ValueError("AI output was not a JSON object")
            return {**parsed,"ai_mode":"OPENAI_RESPONSES","model":self.settings.openai_model,"generated_at":time.time()}
        except Exception as exc:
            return {**fallback,"ai_mode":"DETERMINISTIC_FALLBACK_AFTER_ERROR","ai_error":str(exc)[:240],"model":self.settings.openai_model,"generated_at":time.time()}

    def portfolio_brief(self) -> dict[str, Any]:
        positions=[p for p in self.store.list_positions() if str(p.get("monitoring_class") or "").upper() != "ARCHIVED_SUPERSEDED"]
        open_positions=[p for p in positions if str(p.get("status")).upper()=="OPEN"]
        ranked=sorted([{"position":enrich_position(p),"risk":edge_risk(p)} for p in open_positions],key=lambda r:r["risk"]["score"],reverse=True)
        top=ranked[:5]; summary=portfolio_summary(positions)
        opportunities=[]
        for op in self.store.list_opportunities(20):
            candidate=op.get("candidate") or {}; evaluation=op.get("evaluation") or {}; econ=evaluation.get("quick_economics") or {}
            opportunities.append({"chain":op.get("chain"),"pair":op.get("pair"),"pool_address":op.get("pool_address"),"preferred_sleeve":op.get("preferred_sleeve") or evaluation.get("preferred_sleeve"),"preferred_score":op.get("preferred_score"),"core_pre_score":evaluation.get("core_pre_score"),"tactical_pre_score":evaluation.get("tactical_pre_score"),"quality_flags":evaluation.get("quality_flags") or [],"economics":econ,"tvl_usd":candidate.get("tvl_usd"),"volume_24h_usd":candidate.get("volume_24h_usd")})
        best=opportunities[0] if opportunities else None
        if open_positions:
            deterministic_rec="Manage any material position risk, then compare fresh opportunities for spare capital."
        elif best:
            deterministic_rec=f"No LP capital is deployed. Investigate {best.get('pair')} on {best.get('chain')} now and compare at least one Core and one Tactical candidate before leaving capital idle."
        else:
            deterministic_rec="No LP capital is deployed. Run Scout now; do not treat an empty opportunity cache as evidence that cash is optimal."
        fallback={
            "headline":"Capital is idle; find the best admissible fee opportunity" if not open_positions else ("Portfolio operating normally" if not top or top[0]["risk"]["score"] < 70 else "Portfolio action may be required"),
            "status":"WATCH" if not open_positions else ("GREEN" if not top or top[0]["risk"]["score"] < 50 else "AMBER" if top[0]["risk"]["score"] < 80 else "RED"),
            "confidence":82,"recommendation":deterministic_rec,
            "market_sentiment":"Use each candidate's Strategy Lab regime/sentiment evidence; regime changes range design rather than automatically blocking LP deployment.",
            "profit_case":"The objective is sustainable fee operating return. Idle capital must be justified against the best currently admissible Core/Tactical candidate, not treated as the default safe answer.",
            "reasons":[f"{len(open_positions)} open positions",f"{len(opportunities)} cached/scouted opportunities available for comparison"],
            "risks":[f"{row['position'].get('display_name') or row['position'].get('pair')}: {row['risk'].get('state')}" for row in top if row['risk']["score"]>=48],
            "counterargument":"Deploying merely to avoid idle cash is wrong when pool integrity or fee economics are weak.","invalidation":"Reject a candidate on concrete liquidity/token/protocol/economic evidence, not simply because the market is trending.","next_review":"Run Scout/Strategy Lab now if capital is idle; otherwise routine sleeve cadence or material event.",
        }
        context={"summary":summary,"positions":top,"opportunities":opportunities[:10],"recent_decisions":self.store.list_decisions(10)}
        return self._ask("Produce the current portfolio brief.",context,fallback,purpose="PORTFOLIO_BRIEF")

    def position_review(self, position: dict[str, Any]) -> dict[str, Any]:
        decision=deterministic_plan(position); risk=edge_risk(position); enriched=enrich_position(position)
        fallback={"headline":decision.summary,"status":risk.get("state","WATCH"),"confidence":round(decision.confidence*100 if decision.confidence<=1 else decision.confidence),"recommendation":decision.action,"market_sentiment":"Not independently researched for this fallback review.","profit_case":f"Current recorded APR {float(position.get('apr_current') or 0):.1f}% with range-risk score {risk.get('score')}; economics should be re-evaluated before moving capital.","reasons":[decision.rationale],"risks":[f"Edge risk score {risk.get('score')}"],"counterargument":"Price may revert before action becomes necessary.","invalidation":"Thesis, liquidity or range state materially changes.","next_review":"Use sleeve monitoring cadence."}
        return self._ask("Review this live LP position and recommend the next management action.",{"position":enriched,"risk":risk},fallback,purpose="POSITION_REVIEW")

    def opportunity_memo(self, pool: dict[str, Any], lab: dict[str, Any] | None = None) -> dict[str, Any]:
        evaluation=pool.get("evaluation") or {}
        preferred=evaluation.get("preferred_sleeve")
        best=(lab or {}).get("recommended_range") or {}
        econ=(lab or {}).get("economics") or best.get("economics") or {}
        net=econ.get("estimated_net_month_usd")
        fallback={"headline":f"{pool.get('pair','Pool')} opportunity review","status":"CANDIDATE" if preferred else "WATCH","confidence":75 if preferred else 55,"recommendation":f"Consider as {preferred}" if preferred else "Gather more evidence before allocating capital","market_sentiment":str(((lab or {}).get("regime") or {}).get("label") or "NOT_RESEARCHED"),"profit_case":(f"Estimated net month ${float(net):,.2f} on ${float((lab or {}).get('capital') or econ.get('capital_usd') or 0):,.0f} modelled capital." if net is not None else "Profit case remains incomplete until fee/range economics are available."),"reasons":[f"TVL ${float(pool.get('tvl_usd') or 0):,.0f}",f"24h volume ${float(pool.get('volume_24h_usd') or 0):,.0f}"] + ([f"Top historical range score {best.get('score'):.1f}"] if best else []),"risks":((evaluation.get("risk_core") or {}).get("blockers") or [])[:3],"counterargument":"Current pool activity may not persist through the intended holding horizon.","invalidation":"Liquidity/volume deterioration, adverse sentiment/regime change, or range durability below sleeve threshold.","next_review":"After replay/range analysis or a material market event."}
        return self._ask("Write an LP investment memo for this opportunity. Use the supplied strategy-lab evidence when present.",{"pool":pool,"strategy_lab":lab or {}},fallback,purpose="OPPORTUNITY_MEMO")
    def allocation_memo(self, allocation: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        rows=allocation.get("allocations") or []
        top=rows[0] if rows else {}
        fallback={
            "headline":"Capital allocation review",
            "status":"CANDIDATE" if rows else "WATCH",
            "confidence":72 if rows else 60,
            "recommendation":(f"Prioritise {top.get('pair')} with {top.get('amount')} modelled capital while preserving reserve." if rows else "Keep capital in reserve until an opportunity clears the threshold."),
            "market_sentiment":"Per-asset current sentiment requires live research; technical regime evidence is included in candidate data when available.",
            "profit_case":(f"Modelled allocation expects approximately {sum(float(x.get('expected_net_month') or 0) for x in rows):.2f} net/month across selected positions." if rows else "No current candidate earns deployment under the configured guardrails."),
            "reasons":[f"{len(rows)} positions selected",f"Reserve/unallocated {allocation.get('reserve',0)}"],
            "risks":["Economics are model estimates until exact active-liquidity/fee-growth history is reconstructed."],
            "counterargument":"Concentrating capital in the top-ranked pool can increase pair/protocol/regime concentration risk.",
            "invalidation":"Material deterioration in liquidity, volume, market regime, token thesis or protocol risk.",
            "next_review":"Re-run after a material market event or before committing capital.",
        }
        return self._ask("Compare these LP opportunities as a portfolio allocator. Challenge the deterministic ranking if concentration, sentiment or correlated exposure makes it inferior. Focus on expected NET profit, not headline APR.",{"allocation":allocation,"candidates":candidates},fallback,purpose="PORTFOLIO_ALLOCATION")
