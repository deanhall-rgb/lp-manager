from __future__ import annotations

import time
import uuid
import math
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .allocation import AllocationLimits, suggest_allocation
from .analytics import enrich_position, portfolio_by_sleeve, portfolio_summary, range_metrics
from .compounding import fee_action
from .config import load_settings
from .db import Store
from .event_engine import detect_material_event, review_due
from .execution import ExecutionService
from .legacy_bridge import discover_legacy_sources, import_campaign_ledger
from .live_service import LiveDataService
from .live_scout import preliminary_pool_evaluation
from .strategy_lab import analyse_live_pool
from .intelligence import IntelligenceService
from .automation_policy import get_policy as get_automation_policy, update_policy as update_automation_policy
from .chain_registry import CHAINS
from .fx import money_context, display_amount_to_usd
from .portfolio_policy import policies_payload
from .risk_engine import assess_pool_risk
from .replay import demo_replay_candles, run_replay
from .notification_policy import evaluate_notification
from .support_bundle import build_support_bundle
from .range_lab import analyse_range, rank_range_candidates
from .scout_policy import ScoutCandidate, evaluate_candidate
from .scout_registry import scout_universe
from .seed import seed_demo
from .strategy import deterministic_plan, edge_risk
from .targets import performance_targets
from .transaction_plan import ALLOWED_ACTIONS
from .economics_engine import estimate_lp_economics
from .market_regime import analyse_regime
from .portfolio_advisor import rank_opportunities
from .models import Decision
from .historical_import import import_delta_pool_history
from .pool_chain import read_v3_pool_metadata
from .profit_engine import recommend_profit_range
from .profit_dashboard import portfolio_profit_scorecard
from .profit_calibration import fee_calibration_for_pool, calibration_samples
from .capital_allocation import rank_capital_candidates
from .portfolio_accounting import position_accounting
from .fee_metrics import observed_fee_metrics


class ScoutIntent(BaseModel):
    chain: str
    protocol: str
    pair: str
    pool_address: str | None = None
    tvl_usd: float = 0.0
    volume_24h_usd: float = 0.0
    pool_age_days: float = 0.0
    chain_quality: float = 50.0
    asset_conviction: float = 50.0
    token_quality: float = 50.0
    liquidity_stability: float = 50.0
    fee_consistency: float = 50.0
    in_range_probability_30d: float = 0.0
    expected_monthly_net_pct: float = 0.0
    expected_daily_net_pct: float = 0.0
    historical_volatility: float = 0.0
    gas_drag_pct: float = 0.0
    sentiment_score: float = 0.0
    momentum_alignment: float = 50.0
    downside_inventory_desirable: bool = False
    upside_inventory_desirable: bool = True
    protocol_quality: float | None = None
    liquidity_concentration_risk: float = 50.0
    contract_risk: float = 25.0
    stablecoin_risk: float = 15.0
    exit_liquidity_score: float | None = None
    audited_contract: bool | None = None


class ReplayIntent(BaseModel):
    scenario: str = "CUSTOM"
    candles: list[dict[str, Any]] = Field(default_factory=list)
    sleeve: str = "CORE_INCOME"
    pair: str = "WETH/USDC"
    chain: str = "BASE"
    protocol: str = "UNISWAP_V3"
    warmup_candles: int = 72
    lookback_candles: int | None = None
    initial_capital: float = 1000.0
    inventory_intent: str = "BALANCED"
    future_audit_candles: int = 24


class RiskIntent(BaseModel):
    sleeve: str
    candidate: dict[str, Any]


class NotificationIntent(BaseModel):
    event: dict[str, Any]
    last_sent_at: float | None = None
    now: float | None = None
    minimum_severity: str = "WATCH"


class RangeIntent(BaseModel):
    candles: list[dict[str, Any]]
    lower: float | None = None
    upper: float | None = None
    spot: float | None = None
    sleeve: str = "CORE_INCOME"
    horizon_days: float | None = None


class FeeIntent(BaseModel):
    sleeve: str
    unclaimed_fees_value: float
    collect_cost: float
    reinvest_cost: float = 0.0
    expected_apr_pct: float = 0.0
    horizon_days: float = 30.0
    bank_profit: bool | None = None


class EventIntent(BaseModel):
    strategy_sleeve: str
    previous: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any]
    last_strategy_review_at: float | None = None
    last_ai_review_at: float | None = None
    now: float | None = None


class AllocationIntent(BaseModel):
    total_portfolio_value: float
    available_cash: float
    opportunity: dict[str, Any]
    limits: dict[str, float] | None = None


class OpportunityStatusIntent(BaseModel):
    status: str


class TargetIntent(BaseModel):
    capital: float
    target_monthly_pct: float
    actual_today: float = 0.0
    actual_7d: float = 0.0
    actual_30d: float = 0.0


class LiveRefreshIntent(BaseModel):
    chains: list[str] | None = None


class OpeningTransactionsIntent(BaseModel):
    chain: str = "ROBINHOOD_CHAIN"
    transaction_hashes: list[str] = Field(default_factory=list, min_length=1, max_length=20)


class PoolReplayIntent(BaseModel):
    days: int = 30
    sleeve: str = "CORE_INCOME"
    initial_capital: float = 1000.0
    target_monthly_pct: float = 10.0


class OpenIntent(BaseModel):
    pair: str
    chain: str | None = None
    strategy_sleeve: str = "TACTICAL_CAMPAIGN"
    directional_bias: str = "NEUTRAL"
    inventory_intent: str = "BALANCED"
    target_hold_days: float | None = None
    lower_price: float
    upper_price: float
    capital_value: float
    fee_tier: int | None = None
    protocol: str = "UNISWAP_V3"


class StrategyLabIntent(BaseModel):
    chain: str
    pool_address: str
    sleeve: str = "CORE_INCOME"
    days: int = 90
    capital: float = 1000.0
    target_monthly_pct: float = 10.0


class ProfitRangeIntent(BaseModel):
    chain: str
    pool_address: str
    horizon_days: float = 7.0
    capital: float = 1000.0
    sleeve: str = "AUTO"
    monthly_target_pct: float = 10.0
    history_days: int | None = None


class ProfitSearchIntent(BaseModel):
    available_capital: float = 1000.0
    horizon_days: float = 7.0
    sleeve: str = "AUTO"
    monthly_target_pct: float = 10.0
    reserve_pct: float = 10.0
    chains: list[str] | None = None
    max_candidates: int = 4


class PortfolioAdvisorIntent(BaseModel):
    available_capital: float = 1000.0
    reserve_pct: float = 10.0
    chains: list[str] | None = None
    max_positions: int = 4
    sleeve_filter: str = "ANY"
    allocation_mode: str = "DIVERSIFIED"




class ExecutionOpenIntent(BaseModel):
    chain: str
    pool_address: str
    lower_price: float
    upper_price: float
    amount0: float = 0.0
    amount1: float = 0.0
    slippage_bps: int = 100
    ttl_seconds: int = 1200
    wrap_native_amount: float = 0.0

class ExecutionQuoteIntent(BaseModel):
    chain: str
    pool_address: str
    lower_price: float
    upper_price: float
    known_side: int = 0
    known_amount: float = 0.0

class AutomationPolicyIntent(BaseModel):
    changes: dict[str, bool] = Field(default_factory=dict)


class PositionMetadataIntent(BaseModel):
    display_name: str | None = None
    campaign_label: str | None = None
    entry_thesis: str | None = None
    exit_goal: str | None = None
    lifecycle_stage: str | None = None
    strategy_sleeve: str | None = None
    directional_bias: str | None = None
    inventory_intent: str | None = None
    target_hold_days: float | None = None
    monitoring_class: str | None = None
    notes: str | None = None


def create_app(project_root: Path | None = None) -> FastAPI:
    settings = load_settings(project_root)
    store = Store(settings.database_path)
    if settings.demo_seed:
        seed_demo(store)
    else:
        store.purge_demo_positions()
    if settings.auto_import_legacy and settings.legacy_root.exists():
        try: import_campaign_ledger(store, settings.legacy_root)
        except Exception: pass
    # This personal release carries a compact evidence summary derived from the
    # supplied DELTA pool reconstruction. Import it after the old campaign ledger
    # so the authoritative NFT identities archive misleading legacy LP1/2/3 rows.
    bundled_delta = Path(__file__).resolve().parent / "reference_data" / "delta_history_summary.json"
    if bundled_delta.exists():
        try:
            import json as _json
            import_delta_pool_history(store, _json.loads(bundled_delta.read_text(encoding="utf-8")))
        except Exception:
            pass
    executor = ExecutionService(settings, store)
    live = LiveDataService(settings, store)
    intelligence = IntelligenceService(settings, store)

    def _visible_positions(status: str | None = None) -> list[dict[str, Any]]:
        rows=store.list_positions(status)
        return [r for r in rows if str(r.get("monitoring_class") or "").upper() != "ARCHIVED_SUPERSEDED"]

    def _display_capital_to_usd(value: float) -> float:
        """UI money inputs are in the configured display currency (GBP by default)."""
        return max(0.0, display_amount_to_usd(float(value or 0), settings, store))

    def _attach_historical_live_context(row: dict[str, Any], snap: dict[str, Any] | None = None, cache: dict[tuple[str,str],dict[str,Any]] | None = None) -> dict[str, Any]:
        """Attach today's pool price to a closed historical position without rewriting history.

        Historical import stores entry/range evidence only. A fresh RPC pool read is
        the sole authority for the 'today vs old range' marker.
        """
        snap=snap or {}
        hist=snap.get("historical_evidence") if isinstance(snap.get("historical_evidence"),dict) else (snap if snap.get("historical_pool_reconstruction") else None)
        if not hist:
            return row
        pool=str(row.get("pool_address") or hist.get("pool_address") or "")
        chain=str(row.get("chain") or "")
        if not pool or not chain:
            return row
        cfg=CHAINS.get(chain.upper())
        if not cfg or cfg.rpc_source()=="PUBLIC_FALLBACK":
            return {**row,"historical_current":{"ok":False,"source":"NO_OPERATOR_RPC","error":"Current historical-range marker requires configured/Alchemy RPC"}}
        key=(chain.upper(),pool.lower()); cache=cache if cache is not None else {}
        if key not in cache:
            cache[key]=read_v3_pool_metadata(chain,pool)
        meta=cache[key]
        ctx={"ok":bool(meta.get("ok")),"read_at":time.time(),"pool_address":pool}
        if meta.get("ok"):
            lens=meta.get("price_lens") or {}; current=float(lens.get("current") or 0)
            probe={**row,"current_price":current}
            ctx.update({"current":current,"unit":lens.get("unit") or row.get("range_unit"),"current_tick":meta.get("current_tick"),"range":range_metrics(probe),"source":"LIVE_RPC_POOL"})
            row={**row,"current_price":current,"range":ctx["range"],"historical_current":ctx}
        else:
            ctx["error"]=meta.get("error")
            row={**row,"historical_current":ctx}
        return row

    def _discover_scout_pools(chain: str, *, max_pages: int = 2, wallet_token_limit: int = 6) -> tuple[list[dict[str,Any]], list[str]]:
        """Broader discovery than GeckoTerminal's single network top page.

        Merge top network pages with pools for tokens actually held in the
        configured wallet, known position pools, and persisted opportunities.
        """
        key=str(chain).upper(); rows: list[dict[str,Any]]=[]; errors: list[str]=[]
        if not live.market:
            return [], ["market data disabled"]
        for page in range(1,max(1,max_pages)+1):
            try: rows.extend(live.market.network_pools(key,page=page))
            except Exception as exc: errors.append(f"network page {page}: {str(exc)[:140]}")
        wallet=store.get_wallet_snapshot() or {}
        token_addresses=[]
        for h in wallet.get("holdings") or []:
            if str(h.get("chain") or "").upper()!=key: continue
            addr=str(h.get("address") or "")
            if addr and addr.lower() not in {a.lower() for a in token_addresses}:
                token_addresses.append(addr)
        for addr in token_addresses[:wallet_token_limit]:
            try: rows.extend(live.market.token_pools(key,addr,page=1))
            except Exception as exc: errors.append(f"wallet token {addr[:8]}: {str(exc)[:120]}")
        known_pools=set()
        for pos in _visible_positions():
            if str(pos.get("chain") or "").upper()==key and pos.get("pool_address"):
                known_pools.add(str(pos.get("pool_address")))
        for op in store.list_opportunities(100):
            if str(op.get("chain") or "").upper()==key and op.get("pool_address"):
                known_pools.add(str(op.get("pool_address")))
        for addr in list(known_pools)[:12]:
            try: rows.append(live.market.pool(key,addr))
            except Exception: pass
        dedup={}
        for row in rows:
            addr=str(row.get("pool_address") or "").lower()
            if addr: dedup[addr]=row
        return list(dedup.values()), errors

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        live.start_background()
        try:
            yield
        finally:
            live.stop_background()

    app = FastAPI(title="LP Manager", version="0.8.6", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "version": "0.8.6",
            "server_time": time.time(),
            "database": str(settings.database_path),
            "execution": executor.capabilities(),
            "live": live.status(),
            "ai": intelligence.status(),
        }

    @app.get("/api/overview")
    def overview():
        positions = _visible_positions()
        enriched = []
        for p in positions:
            row = enrich_position(p)
            row["risk"] = edge_risk(p)
            snap = store.get_position_snapshot(str(p.get("id")))
            if snap:
                row["live_snapshot"] = snap
            tracker=store.get_setting(f"fees:tracker:{p.get('id')}", {}) or {}
            row["accounting"]=position_accounting(p,snap or {},tracker)
            row["fee_metrics"]=observed_fee_metrics(tracker,float(p.get("capital_value") or p.get("current_value") or 0))
            enriched.append(row)
        open_positions = [p for p in enriched if p["status"] == "OPEN"]
        attention = sorted(
            [{"position": p, "risk": edge_risk(p)} for p in open_positions],
            key=lambda row: row["risk"]["score"], reverse=True,
        )
        return {
            "summary": portfolio_summary(positions),
            "money": money_context(settings, store),
            "wallet": store.get_wallet_snapshot(),
            "portfolio_by_sleeve": portfolio_by_sleeve(positions),
            "strategy_profiles": policies_payload(),
            "scout_universe": scout_universe(),
            "opportunities": store.list_opportunities(20),
            "replay_runs": store.list_replay_runs(12),
            "outcome_audits": store.list_outcome_audits(12),
            "positions": enriched,
            "attention": attention,
            "decisions": store.list_decisions(100),
            "actions": store.list_actions(12),
            "execution": executor.capabilities(),
            "legacy_sources": discover_legacy_sources(settings.legacy_root),
            "live": live.status(),
            "ai": intelligence.status(),
            "automation": get_automation_policy(store),
            "profit_scorecard": portfolio_profit_scorecard(store),
            "profit_last_recommendation": store.get_setting("profit:last_recommendation", None),
        }

    @app.get("/api/profit/scorecard")
    def profit_scorecard():
        return portfolio_profit_scorecard(store)

    @app.get("/api/profit/calibration")
    def profit_calibration():
        return {"samples": calibration_samples(store)}

    @app.post("/api/profit/recommend")
    def profit_recommend(intent: ProfitRangeIntent):
        if not live.market:
            raise HTTPException(503, "Market data is disabled")
        pool_fallback=None
        for op in store.list_opportunities(300):
            candidate=dict(op.get("candidate") or {})
            if str(candidate.get("pool_address") or "").lower()==str(intent.pool_address or "").lower() and str(candidate.get("chain") or op.get("chain") or "").upper()==intent.chain.upper():
                pool_fallback=candidate; break
        try:
            result=recommend_profit_range(
                live.market, store, intent.chain.upper(), intent.pool_address,
                horizon_days=max(1/24.0,min(90.0,float(intent.horizon_days))),
                capital=max(1.0,_display_capital_to_usd(intent.capital)), sleeve=intent.sleeve,
                monthly_target_pct=max(0.0,float(intent.monthly_target_pct)),
                history_days=intent.history_days, pool_fallback=pool_fallback,
            )
            result["generated_at"]=time.time()
            result["data_status"]="LIVE_OR_FRESH_HISTORY"
            store.set_setting("profit:last_recommendation", result)
            cache_key=f"profit:last:{intent.chain.upper()}:{intent.pool_address.lower()}:{round(float(intent.horizon_days),3)}:{str(intent.sleeve).upper()}"
            store.set_setting(cache_key,result)
            return result
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503,f"Profit recommendation unavailable: {str(exc)[:280]}") from exc

    @app.post("/api/profit/search")
    def profit_search(intent: ProfitSearchIntent):
        if not live.market:
            raise HTTPException(503, "Market data is disabled")
        capital=max(1.0,_display_capital_to_usd(intent.available_capital))
        chains=[str(x).upper() for x in (intent.chains or ["ETHEREUM","BASE","ARBITRUM","ROBINHOOD_CHAIN"])]
        prelim=[]; errors=[]
        for chain in chains[:5]:
            try:
                pools, discovery_errors=_discover_scout_pools(chain,max_pages=1)
                errors.extend({"chain":chain,"error":e} for e in discovery_errors[:2])
            except Exception as exc:
                errors.append({"chain":chain,"error":str(exc)[:180]}); continue
            for pool in pools:
                if str(pool.get("protocol") or "").upper()!="UNISWAP_V3": continue
                ev=preliminary_pool_evaluation(pool)
                sleeve_pref=str(intent.sleeve or "AUTO").upper()
                if sleeve_pref in {"AUTO","ANY",""}:
                    score=max(float(ev.get("core_pre_score") or 0),float(ev.get("tactical_pre_score") or 0))
                elif sleeve_pref=="CORE_INCOME": score=float(ev.get("core_pre_score") or 0)
                else: score=float(ev.get("tactical_pre_score") or 0)
                prelim.append((score,float(pool.get("tvl_usd") or 0),pool,ev))
        prelim.sort(key=lambda x:(x[0],x[1]),reverse=True)
        deep=[]
        for score,_tvl,pool,ev in prelim[:max(1,min(8,int(intent.max_candidates)))]:
            try:
                r=recommend_profit_range(
                    live.market,store,str(pool.get("chain") or "").upper(),str(pool.get("pool_address") or ""),
                    horizon_days=max(1/24.0,min(90.0,float(intent.horizon_days))),capital=capital,
                    sleeve=intent.sleeve,monthly_target_pct=max(0.0,float(intent.monthly_target_pct)),pool_fallback=pool,
                    compare_fee_tiers=False,
                )
                b=r.get("recommended_range") or {}; f=b.get("forecast") or {}
                deep.append({
                    "chain":r.get("chain"),"pair":r.get("pair"),"pool_address":r.get("pool_address"),"sleeve":r.get("sleeve"),
                    "confidence":r.get("confidence"),"profit_score":b.get("profit_score"),"range":{"lower":b.get("lower"),"upper":b.get("upper"),"price_lens":b.get("price_lens")},
                    "expected_net_usd":f.get("expected_net_usd"),"expected_net_pct":f.get("expected_net_pct"),"expected_fees_usd":f.get("expected_fees_usd"),
                    "low_net_usd":f.get("low_net_usd"),"high_net_usd":f.get("high_net_usd"),"target_attainment_pct":f.get("target_attainment_pct"),
                    "pre_score":round(score,1),"recommendation":r,
                })
            except Exception as exc:
                errors.append({"chain":str(pool.get("chain") or ""),"pair":str(pool.get("pair") or ""),"error":str(exc)[:200]})
        deep=rank_capital_candidates(
            deep,
            capital_usd=capital,
            open_positions=store.list_positions("OPEN"),
        )
        reserve=capital*max(0.0,min(90.0,float(intent.reserve_pct)))/100.0
        deployable=max(0.0,capital-reserve)
        allocations=[]
        eligible=[r for r in deep if float(r.get("expected_net_usd") or 0)>0]
        if eligible and deployable>0:
            weights=[
                max(0.01,float(r.get("allocation_score") or 0))
                * max(0.10,float(r.get("expected_net_pct") or 0))
                for r in eligible[:3]
            ]
            tw=sum(weights) or 1.0
            remaining=deployable
            for i,(row,w) in enumerate(zip(eligible[:3],weights)):
                # Core may carry more portfolio capital; tactical remains capped.
                cap=deployable*(0.70 if row.get("sleeve")=="CORE_INCOME" else 0.35)
                amt=min(cap,deployable*w/tw,remaining)
                if amt<1: continue
                allocations.append({
                    "rank":i+1,"chain":row.get("chain"),"pair":row.get("pair"),
                    "pool_address":row.get("pool_address"),"sleeve":row.get("sleeve"),
                    "amount":round(amt,2),
                    "expected_net_for_hold":round(float(row.get("expected_net_usd") or 0)*amt/capital,2),
                    "allocation_score":row.get("allocation_score"),
                    "allocation_evidence":row.get("allocation_evidence") or {},
                })
                remaining-=amt
        result={"capital":capital,"horizon_days":float(intent.horizon_days),"reserve":round(reserve,2),"deployable":round(deployable,2),"ranked":deep,"allocations":allocations,"errors":errors[:12],"candidate_count":len(prelim),"deep_analysed":len(deep),"best":deep[0] if deep else None,"note":"Deep ranking uses the same Profit Lab engine for each candidate. Cross-chain allocation then compares expected net profit, downside case, fee efficiency, confidence, intervention burden and existing concentration. Provider limits cap simultaneous deep analyses."}
        store.set_setting("profit:last_search",result)
        return result

    @app.get("/api/wallet")
    def wallet_view():
        cached=store.get_wallet_snapshot()
        if cached: return cached
        try:
            from .wallet import build_wallet_snapshot
            return build_wallet_snapshot(settings, store, live.market, include_health=False)
        except ImportError as exc:
            return {"ok":False,"error":f"Wallet dependencies unavailable: {exc}","holdings":[],"chains":[]}

    @app.post("/api/wallet/refresh")
    def wallet_refresh():
        try: return live.refresh_wallet(include_health=True)
        except ImportError as exc: raise HTTPException(503,f"Wallet dependencies unavailable: {exc}") from exc

    @app.get("/api/live/rpc-health")
    def live_rpc_health():
        try:
            from .wallet import rpc_health
            return {"chains":[rpc_health(cfg) for cfg in CHAINS.values()]}
        except ImportError as exc:
            raise HTTPException(503,f"RPC health dependencies unavailable: {exc}") from exc

    @app.get("/api/live/status")
    def live_status():
        return live.status()

    @app.post("/api/live/refresh")
    def live_refresh(intent: LiveRefreshIntent = LiveRefreshIntent()):
        return live.refresh_positions(intent.chains)

    @app.post("/api/live/import-opening-transactions")
    def live_import_opening_transactions(intent: OpeningTransactionsIntent):
        result=live.import_opening_transactions(intent.chain,intent.transaction_hashes)
        if not result.get("ok") and result.get("error"):
            # Keep validation/recovery diagnostics in the JSON body instead of
            # converting provider/transient failures into an opaque browser 502.
            return result
        return result

    @app.get("/api/scout/live/{chain}")
    def live_scout(chain: str, limit: int = 20):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            rows, discovery_errors = _discover_scout_pools(chain.upper(), max_pages=2)
            if not rows:
                raise RuntimeError("No pools returned from top-page, wallet-token or known-pool discovery" + (f": {'; '.join(discovery_errors[:2])}" if discovery_errors else ""))
            provider_status = "LIVE"
            provider_error = "; ".join(discovery_errors[:3]) if discovery_errors else None
        except Exception as exc:
            # Market-data failure must not erase the operator's opportunity view.
            # Fall back to the persistent opportunity book for this chain and mark
            # it degraded/stale rather than turning the page into a 502.
            cached=[]
            for op in store.list_opportunities(100):
                if str(op.get("chain") or "").upper() != chain.upper():
                    continue
                candidate=dict(op.get("candidate") or {})
                evaluation=dict(op.get("evaluation") or {})
                candidate["evaluation"] = evaluation
                candidate["sleeve"] = evaluation.get("preferred_sleeve") or op.get("preferred_sleeve")
                candidate["quick_economics"] = evaluation.get("quick_economics") or {}
                candidate["market_data_status"] = "STALE_PERSISTED"
                cached.append(candidate)
            cached.sort(key=lambda r:max(float((r.get("evaluation") or {}).get("core_pre_score") or 0),float((r.get("evaluation") or {}).get("tactical_pre_score") or 0)),reverse=True)
            return {"chain":chain.upper(),"count":len(cached[:max(1,min(100,limit))]),"pools":cached[:max(1,min(100,limit))],"provider_status":"DEGRADED","provider_error":str(exc)[:300],"stale":True}
        out=[]
        for row in rows:
            if str(row.get("protocol") or "").upper() != "UNISWAP_V3":
                continue
            evaluation = preliminary_pool_evaluation(row)
            preferred=evaluation.get("preferred_sleeve") or ("CORE_INCOME" if float(evaluation.get("core_pre_score") or 0)>=float(evaluation.get("tactical_pre_score") or 0) else "TACTICAL_CAMPAIGN")
            quick=estimate_lp_economics(row,capital=1000.0,active_time_pct=82.0 if preferred=="CORE_INCOME" else 58.0,width_pct=50.0 if preferred=="CORE_INCOME" else 22.0,regime={})
            enriched={**row,"evaluation":evaluation,"sleeve":preferred,"quick_economics":quick}
            out.append(enriched)
            try:
                store.upsert_opportunity(candidate=row,evaluation={**evaluation,"quick_economics":quick},status="CANDIDATE" if evaluation.get("preferred_sleeve") else "WATCH")
            except Exception:
                pass
        out.sort(key=lambda r: (float((r.get("evaluation") or {}).get("core_pre_score") or 0),float(r.get("tvl_usd") or 0)), reverse=True)
        result=out[:max(1,min(100,limit))]
        if result:
            top=result[0]; hour=int(time.time()//3600)
            try:
                store.add_decision(Decision(id=uuid.uuid5(uuid.NAMESPACE_URL,f"scout:{chain.upper()}:{top.get('pool_address')}:{hour}").hex,position_id=None,created_at=time.time(),severity="INFO",action="OPPORTUNITY_REVIEW",confidence=max(float((top.get("evaluation") or {}).get("core_pre_score") or 0),float((top.get("evaluation") or {}).get("tactical_pre_score") or 0))/100.0,summary=f"{top.get('pair')} currently leads the {chain.upper()} scout",rationale=f"Current pool quality, TVL and activity rank it highest in this scan; economics shown are estimates pending Strategy Lab.",trigger="SCOUT_REFRESH",source="SCOUT",evidence={"chain":chain.upper(),"pair":top.get("pair"),"pool":top.get("pool_address"),"sleeve":top.get("sleeve"),"est_month_per_1000":(top.get("quick_economics") or {}).get("estimated_operating_net_month_usd",(top.get("quick_economics") or {}).get("estimated_net_month_usd"))}))
            except Exception:
                pass
        return {"chain": chain.upper(), "count": len(result), "pools": result, "provider_status":provider_status, "provider_error":provider_error, "stale":False}

    @app.get("/api/scout/pools/{chain}/{address}")
    def live_pool_detail(chain: str, address: str):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            resolved,row = live.market.resolve_pool(chain.upper(), address) if hasattr(live.market,"resolve_pool") else (chain.upper(),live.market.pool(chain.upper(), address))
            onchain=read_v3_pool_metadata(resolved,address)
            if onchain.get("ok"):
                row={**row,"fee_tier":onchain.get("fee_tier"),"fee_tier_bps":onchain.get("fee_tier_bps"),"tick_spacing":onchain.get("tick_spacing"),"onchain":onchain}
            candles = live.market.ohlcv_days(resolved, address, 14)
            regime=analyse_regime(candles)
            evaluation=preliminary_pool_evaluation(row)
            row["evaluation"] = evaluation
            row["resolved_chain"] = resolved
            row["chain_mismatch"] = resolved != chain.upper()
            row["ohlcv_7d"] = candles[-168:]
            row["regime"] = regime
            row["economics_per_1000"] = {
                "core": estimate_lp_economics(row,capital=1000,active_time_pct=85,width_pct=50,regime=regime),
                "tactical": estimate_lp_economics(row,capital=1000,active_time_pct=60,width_pct=22,regime=regime),
            }
            return row
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/scout/pools/{chain}/{address}/ohlcv")
    def live_pool_ohlcv(chain: str, address: str, days: int = 30):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            candles = live.market.ohlcv_days(chain.upper(), address, max(1, min(365, days)))
            return {"chain": chain.upper(), "pool_address": address, "days": days, "candles": candles}
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/scout/pools/{chain}/{address}/replay")
    def live_pool_replay(chain: str, address: str, intent: PoolReplayIntent):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            resolved,pool = live.market.resolve_pool(chain.upper(),address) if hasattr(live.market,"resolve_pool") else (chain.upper(),live.market.pool(chain.upper(),address))
            candles = live.market.ohlcv_days(resolved, address, max(3, min(365, intent.days)))
            if len(candles) < 30:
                raise ValueError(f"Only {len(candles)} historical candles were returned")
            warmup = min(max(24, len(candles)//4), max(24, len(candles)-24))
            result = run_replay(candles, sleeve=intent.sleeve, pair=str(pool.get("pair") or "POOL"), chain=resolved, protocol=str(pool.get("protocol") or "UNISWAP_V3"), warmup_candles=warmup, initial_capital=intent.initial_capital,pool_context=pool,target_monthly_pct=intent.target_monthly_pct)
            saved = store.save_replay_run(result, scenario=f"LIVE_{intent.days}D")
            return {"pool": pool, "saved": saved, "result": result}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/strategy-profiles")
    def strategy_profiles():
        return policies_payload()

    @app.get("/api/scout/universe")
    def scout_targets():
        return scout_universe()

    @app.get("/api/scout/opportunities")
    def scout_opportunities():
        return store.list_opportunities(100)

    @app.post("/api/scout/evaluate")
    def scout_evaluate(intent: ScoutIntent):
        payload = intent.model_dump()
        candidate_fields = set(ScoutCandidate.__dataclass_fields__)
        candidate = ScoutCandidate(**{k: v for k, v in payload.items() if k in candidate_fields})
        evaluation = evaluate_candidate(candidate)
        preferred = evaluation.get("preferred_sleeve")
        risk = assess_pool_risk(payload, sleeve=preferred or intent.model_dump().get("strategy_sleeve") or "TACTICAL_CAMPAIGN")
        evaluation["risk"] = risk
        if preferred and not risk.get("eligible"):
            evaluation["risk_blocked_preferred_sleeve"] = True
        saved = store.upsert_opportunity(
            candidate=payload,
            evaluation=evaluation,
            status="CANDIDATE" if preferred and risk.get("eligible") else "REJECTED",
        )
        return {"candidate": payload, "evaluation": evaluation, "stored": saved}

    @app.post("/api/scout/opportunities/{opportunity_id}/status")
    def scout_status(opportunity_id: str, intent: OpportunityStatusIntent):
        status = intent.status.upper()
        if status not in {"WATCH", "CANDIDATE", "APPROVED", "REJECTED", "EXPIRED"}:
            raise HTTPException(400, "Invalid opportunity status")
        row = store.set_opportunity_status(opportunity_id, status)
        if not row:
            raise HTTPException(404, "Opportunity not found")
        return row

    @app.post("/api/replay/run")
    def replay_run(intent: ReplayIntent):
        candles = intent.candles
        scenario = intent.scenario.upper()
        sleeve = intent.sleeve
        pair = intent.pair
        if not candles:
            if scenario not in {"CORE_TREND", "TACTICAL_BREAKOUT"}:
                raise HTTPException(400, "Provide candles or a supported demo scenario")
            candles = demo_replay_candles(scenario)
            if scenario == "TACTICAL_BREAKOUT":
                sleeve = "TACTICAL_CAMPAIGN"
                pair = "SYNTHETIC_TACTICAL/ETH"
        try:
            result = run_replay(
                candles, sleeve=sleeve, pair=pair, chain=intent.chain, protocol=intent.protocol,
                warmup_candles=intent.warmup_candles, lookback_candles=intent.lookback_candles,
                initial_capital=intent.initial_capital, inventory_intent=intent.inventory_intent,
                future_audit_candles=intent.future_audit_candles,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        saved = store.save_replay_run(result, scenario=scenario)
        for decision in result.get("decisions", []):
            audit = decision.get("audit") or {}
            outcome = audit.get("outcome") or {}
            if outcome.get("samples", 0):
                store.record_outcome_audit(
                    subject_type="REPLAY_DECISION", subject_id=f"{result['id']}:{decision.get('index')}",
                    horizon=f"{intent.future_audit_candles}_CANDLES", verdict=str(audit.get("verdict") or "UNSCORED"), payload=decision,
                )
        return {"saved": saved, "result": result}

    @app.get("/api/replay/runs")
    def replay_runs():
        return store.list_replay_runs(100)

    @app.get("/api/replay/runs/{replay_id}")
    def replay_detail(replay_id: str):
        row = store.get_replay_run(replay_id)
        if not row:
            raise HTTPException(404, "Replay not found")
        return row

    @app.post("/api/strategy-lab/analyse")
    def strategy_lab(intent: StrategyLabIntent):
        if not live.market: raise HTTPException(503, "Market data is disabled")
        days=max(7,min(365,intent.days)); sleeve=str(intent.sleeve or "CORE_INCOME").upper()
        cache_key=f"strategy:last:{intent.chain.upper()}:{intent.pool_address.lower()}:{sleeve}:{days}"
        try:
            pool_fallback=None
            for op in store.list_opportunities(250):
                candidate=dict(op.get("candidate") or {})
                if str(candidate.get("pool_address") or "").lower()==str(intent.pool_address or "").lower() and str(candidate.get("chain") or op.get("chain") or "").upper()==intent.chain.upper():
                    pool_fallback=candidate; break
            result=analyse_live_pool(
                live.market, intent.chain.upper(), intent.pool_address,
                sleeve=sleeve, days=days,
                capital=max(1.0,_display_capital_to_usd(intent.capital)), target_monthly_pct=max(0.0,intent.target_monthly_pct),
                pool_fallback=pool_fallback, store=store,
            )
            result["data_status"]="LIVE_OR_FRESH_CACHE"
            result["analysed_at"]=time.time()
            store.set_setting(cache_key,result)
            return result
        except Exception as exc:
            cached=store.get_setting(cache_key,None)
            if isinstance(cached,dict):
                fallback=dict(cached); fallback["data_status"]="STALE_STRATEGY_FALLBACK"; fallback["provider_warning"]=str(exc)[:240]
                return fallback
            if isinstance(exc,ValueError):
                raise HTTPException(400, str(exc)) from exc
            raise HTTPException(503, f"Historical market feed temporarily unavailable: {str(exc)[:240]}") from exc

    @app.get("/api/ai/status")
    def ai_status():
        return intelligence.status()

    @app.post("/api/ai/portfolio-brief")
    def ai_portfolio_brief():
        return intelligence.portfolio_brief()

    @app.post("/api/ai/opportunity/{chain}/{address}")
    def ai_opportunity(chain: str, address: str, sleeve: str = "CORE_INCOME", days: int = 90, capital: float = 1000.0):
        if not live.market:
            raise HTTPException(503, "Market data is disabled")
        # Opportunity AI is deliberately resilient: a historical-provider
        # failure must not strand the UI on "Investigating…". Resolve the
        # pool first, then attach whatever Strategy Lab evidence is available.
        try:
            resolved, pool = live.market.resolve_pool(chain.upper(), address) if hasattr(live.market, "resolve_pool") else (chain.upper(), live.market.pool(chain.upper(), address))
        except Exception as exc:
            raise HTTPException(502, f"Pool lookup failed: {exc}") from exc
        pool["evaluation"] = preliminary_pool_evaluation(pool)
        pool["resolved_chain"] = resolved
        capital_usd=max(1.0,_display_capital_to_usd(capital))
        lab_error = None
        try:
            lab = analyse_live_pool(
                live.market, resolved, address, sleeve=sleeve,
                days=max(7, min(365, days)), capital=capital_usd, pool_fallback=pool, store=store,
            )
        except Exception as exc:
            lab_error = str(exc)[:300]
            lab = {
                "analysis_error": lab_error,
                "sleeve": sleeve,
                "capital": capital_usd,
                "days": max(7, min(365, days)),
                "economics": estimate_lp_economics(
                    pool, capital=capital_usd,
                    active_time_pct=82.0 if sleeve.upper()=="CORE_INCOME" else 58.0,
                    width_pct=50.0 if sleeve.upper()=="CORE_INCOME" else 22.0,
                    regime={},
                ),
            }
        memo = intelligence.opportunity_memo(pool, lab)
        if lab_error:
            memo["analysis_warning"] = f"Historical Strategy Lab evidence unavailable: {lab_error}"
        return memo

    @app.post("/api/portfolio-advisor")
    def portfolio_advisor(intent: PortfolioAdvisorIntent):
        if not live.market:
            raise HTTPException(503, "Market data is disabled")
        chains = [str(x).upper() for x in (intent.chains or ["ETHEREUM","BASE","ARBITRUM","OPTIMISM","ROBINHOOD_CHAIN"])]
        candidates: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        # Build a cross-chain comparison universe. This deliberately uses market
        # data rather than RPC availability, so a flaky read-only RPC does not
        # prevent opportunity ranking.
        for chain in chains[:6]:
            try:
                rows, _discovery_errors = _discover_scout_pools(chain, max_pages=2)
                errors.extend({"chain":chain,"error":e} for e in _discovery_errors[:2])
            except Exception as exc:
                errors.append({"chain": chain, "error": str(exc)[:180]})
                continue
            chain_rows=[]
            for pool in rows:
                if str(pool.get("protocol") or "").upper() != "UNISWAP_V3":
                    continue
                evaluation=preliminary_pool_evaluation(pool)
                sleeve=evaluation.get("preferred_sleeve") or ("CORE_INCOME" if float(evaluation.get("core_pre_score") or 0)>=float(evaluation.get("tactical_pre_score") or 0) else "TACTICAL_CAMPAIGN")
                economics=estimate_lp_economics(
                    pool, capital=1000.0,
                    active_time_pct=84.0 if sleeve=="CORE_INCOME" else 60.0,
                    width_pct=48.0 if sleeve=="CORE_INCOME" else 22.0,
                    regime={},
                )
                chain_rows.append({**pool,"evaluation":evaluation,"sleeve":sleeve,"economics":economics,"regime":{"confidence":50,"label":"NOT_YET_DEEP_ANALYSED"}})
            chain_rows.sort(key=lambda r:(float((r.get("evaluation") or {}).get("core_pre_score") or 0),float(r.get("tvl_usd") or 0)),reverse=True)
            candidates.extend(chain_rows[:8])
        result=rank_opportunities(
            candidates, available_capital=max(0.0,_display_capital_to_usd(intent.available_capital)),
            reserve_pct=max(0.0,min(90.0,intent.reserve_pct)),
            max_positions=max(1,min(8,intent.max_positions)),
            sleeve_filter=intent.sleeve_filter, allocation_mode=intent.allocation_mode,
        )
        result["scan_errors"] = errors
        result["candidate_count"] = len(candidates)
        # AI is advisory over the deterministic allocator; if no API key is
        # configured this still returns a deterministic comparative memo.
        result["ai_advice"] = intelligence.allocation_memo(result, candidates[:12])
        if result.get("allocations"):
            top=result["allocations"][0]
            hour=int(time.time()//3600)
            try:
                store.add_decision(Decision(
                    id=uuid.uuid5(uuid.NAMESPACE_URL,f"allocation:{top.get('pool_address')}:{intent.available_capital}:{hour}").hex,
                    position_id=None,created_at=time.time(),severity="INFO",action="PORTFOLIO_ALLOCATION_RECOMMENDATION",
                    confidence=min(0.95,max(0.50,float(top.get("score") or 0)/100.0)),
                    summary=f"Portfolio Advisor currently ranks {top.get('pair')} first",
                    rationale=f"Cross-chain comparison allocated {top.get('amount')} of {intent.available_capital} available capital to the strongest risk-adjusted candidate; reserve and tactical ceilings were preserved.",
                    trigger="PORTFOLIO_ADVISOR",source="PORTFOLIO_ADVISOR",evidence={"pair":top.get("pair"),"chain":top.get("chain"),"sleeve":top.get("sleeve"),"score":top.get("score"),"amount":top.get("amount"),"expected_net_month":top.get("expected_net_month")},
                ))
            except Exception:
                pass
        return result

    @app.get("/api/automation")
    def automation_view():
        return get_automation_policy(store)

    @app.post("/api/automation")
    def automation_update(intent: AutomationPolicyIntent):
        return update_automation_policy(store,intent.changes)

    @app.post("/api/risk/evaluate")
    def risk_evaluate(intent: RiskIntent):
        return assess_pool_risk(intent.candidate, sleeve=intent.sleeve)

    @app.post("/api/notifications/evaluate")
    def notification_evaluate(intent: NotificationIntent):
        return evaluate_notification(intent.event, last_sent_at=intent.last_sent_at, now=intent.now, minimum_severity=intent.minimum_severity)

    @app.get("/api/outcomes")
    def outcomes():
        return store.list_outcome_audits(200)

    @app.get("/api/execution/schema")
    def execution_schema():
        return {"schema_version": "1.0", "allowed_actions": sorted(ALLOWED_ACTIONS), "signing_enabled": False, "broadcast_enabled": False}

    @app.post("/api/support/bundle")
    def support_bundle():
        path = build_support_bundle(
            store, output_dir=settings.data_dir / "support",
            extra={"version":"0.8.6", "execution":executor.capabilities(), "scout_universe":scout_universe()},
        )
        return {"ok": True, "filename": path.name, "download": f"/api/support/bundle/{path.name}"}

    @app.get("/api/support/bundle/{filename}")
    def support_bundle_download(filename: str):
        safe = Path(filename).name
        path = settings.data_dir / "support" / safe
        if not path.exists():
            raise HTTPException(404, "Support bundle not found")
        return FileResponse(path, filename=safe, media_type="application/zip")

    @app.post("/api/range/analyse")
    def range_analyse(intent: RangeIntent):
        if intent.lower is not None and intent.upper is not None:
            horizon = float(intent.horizon_days or (30 if intent.sleeve.upper() == "CORE_INCOME" else 3))
            return analyse_range(intent.candles, intent.lower, intent.upper, horizon_days=horizon)
        if not intent.spot or intent.spot <= 0:
            raise HTTPException(400, "Provide lower+upper or positive spot")
        return {
            "ranked": rank_range_candidates(
                intent.candles,
                intent.spot,
                sleeve=intent.sleeve,
                horizon_days=intent.horizon_days,
            )
        }

    @app.post("/api/fees/decision")
    def fees_decision(intent: FeeIntent):
        return fee_action(**intent.model_dump())

    @app.post("/api/events/evaluate")
    def events_evaluate(intent: EventIntent):
        return {
            "routine": review_due(
                strategy_sleeve=intent.strategy_sleeve,
                last_strategy_review_at=intent.last_strategy_review_at,
                last_ai_review_at=intent.last_ai_review_at,
                now=intent.now,
            ),
            "event": detect_material_event(
                intent.previous,
                intent.current,
                strategy_sleeve=intent.strategy_sleeve,
            ),
        }

    @app.post("/api/allocation/suggest")
    def allocation_suggest(intent: AllocationIntent):
        limits = AllocationLimits(**intent.limits) if intent.limits else AllocationLimits()
        return suggest_allocation(
            total_portfolio_value=intent.total_portfolio_value,
            available_cash=intent.available_cash,
            positions=_visible_positions("OPEN"),
            opportunity=intent.opportunity,
            limits=limits,
        )

    @app.post("/api/targets/calculate")
    def targets_calculate(intent: TargetIntent):
        payload=intent.model_dump()
        payload["capital"]=_display_capital_to_usd(payload.get("capital") or 0)
        payload["actual_today"]=_display_capital_to_usd(payload.get("actual_today") or 0)
        payload["actual_7d"]=_display_capital_to_usd(payload.get("actual_7d") or 0)
        payload["actual_30d"]=_display_capital_to_usd(payload.get("actual_30d") or 0)
        return performance_targets(**payload)

    @app.get("/api/positions")
    def positions():
        out=[]; pool_cache={}
        for p in _visible_positions():
            row=enrich_position(p)
            row["risk"] = edge_risk(p)
            snap=store.get_position_snapshot(str(p.get("id")))
            if snap: row["live_snapshot"]=snap
            row=_attach_historical_live_context(row,snap,pool_cache)
            out.append(row)
        return out

    @app.get("/api/positions/{position_id}")
    def position(position_id: str):
        row = store.get_position(position_id)
        if not row:
            raise HTTPException(404, "Position not found")
        payload={**enrich_position(row), "risk": edge_risk(row)}
        snap=store.get_position_snapshot(position_id)
        if snap: payload["live_snapshot"]=snap
        return _attach_historical_live_context(payload,snap,{})

    @app.post("/api/positions/{position_id}/metadata")
    def position_metadata(position_id: str, intent: PositionMetadataIntent):
        payload={k:v for k,v in intent.model_dump().items() if v is not None}
        row=store.update_position_metadata(position_id, **payload)
        if not row: raise HTTPException(404, "Position not found")
        return enrich_position(row)

    @app.post("/api/positions/{position_id}/campaign-replay")
    def campaign_replay(position_id: str, target_monthly_pct: float = 10.0):
        row=store.get_position(position_id)
        if not row:
            raise HTTPException(404,"Position not found")
        snap=store.get_position_snapshot(position_id) or {}
        historical=snap.get("historical_evidence") if isinstance(snap.get("historical_evidence"),dict) else (snap if snap.get("historical_pool_reconstruction") else None)
        if historical:
            observed=historical.get("observed") or {}; fees=historical.get("fee_evidence") or {}; amounts=historical.get("entry_amounts") or {}; lens=historical.get("price_lens") or {}
            opened=float(row.get("opened_at") or 0); ended=float(row.get("closed_at") or 0) or float((historical.get("raw_position_summary") or {}).get("analysis_end_timestamp") or 0)
            days=max(1/24.0,(ended-opened)/86400.0) if ended>opened>0 else max(1/24.0,float(row.get("target_hold_days") or 1))
            entry_weth=float(amounts.get("entry_weth_equivalent") or 0); fee_weth=float(fees.get("reconstructed_weth_lower_bound") or 0)
            fee_return_pct=(fee_weth/entry_weth*100.0) if entry_weth>0 else 0.0
            annualised=(fee_return_pct/days*365.0) if days>0 else 0.0
            util=float(observed.get("range_utilisation_pct") or 0); fin=historical.get("financial_evidence") or {}
            economics={"mode":"RECONSTRUCTED_ONCHAIN_EVIDENCE","estimated":not bool(fin.get("realised")),"confidence":"HIGH" if fin.get("realised") else "MODERATE","capital_weth_equivalent":entry_weth,"fees_weth_lower_bound":fee_weth,"fee_return_pct_lower_bound":round(fee_return_pct,2),"annualised_fee_rate_pct_lower_bound":round(annualised,1),"replay_days":round(days,2),"range_utilisation_pct":util,"financial_evidence":fin,"assumptions":["Pool-swap fee reconstruction is a lower bound because boundary-crossing allocation is excluded.","Exact fee-claim/close evidence is preferred when present.","Absolute profit and LP-vs-HODL are kept separate; underperforming HODL does not mean the LP lost cash."]}
            result={
                "id":uuid.uuid4().hex,"created_at":time.time(),"scenario":f"HISTORICAL_{row.get('display_name') or position_id}","sleeve":str(row.get("strategy_sleeve") or "TACTICAL_CAMPAIGN"),"pair":str(row.get("pair") or "WETH/DELTA"),"chain":str(row.get("chain") or "ROBINHOOD_CHAIN"),"protocol":str(row.get("protocol") or "UNISWAP_V3"),
                "no_lookahead":True,"replay_type":"HISTORICAL_CAMPAIGN_EVIDENCE","price_lens":lens,
                "range_selection":{"chosen":{"lower":row.get("lower_price"),"upper":row.get("upper_price"),"candidate":{"lower":row.get("lower_price"),"upper":row.get("upper_price")}}},
                "summary":{"active_time_pct":util,"pool_swaps_observed":int(observed.get("swap_count") or 0),"range_exits":int(observed.get("exit_count") or 0),"range_reentries":int(observed.get("reentry_count") or 0),"range_transitions":int(observed.get("exit_count") or 0)+int(observed.get("reentry_count") or 0),"audit_verdict_counts":{},"period_days":round(days,2)},
                "economics":economics,
                "historical_evidence":historical,"decisions":[],
                "campaign_comparison":{"position_id":position_id,"display_name":row.get("display_name") or row.get("pair"),"actual_range":{"lower":row.get("lower_price"),"upper":row.get("upper_price"),"unit":row.get("range_unit")},"entry_timestamp":opened,"observed_end_timestamp":ended,"note":"Observed historical evidence only. Pool swaps are not strategy reviews and no historical AI activity is invented."}
            }
            saved=store.save_replay_run(result,scenario=result["scenario"]); return {"saved":saved,"result":result}
        pool_address=str(row.get("pool_address") or (snap.get("position_snapshot") or {}).get("pool_address") or "")
        if not pool_address or not live.market:
            raise HTTPException(400,"This campaign does not have a replayable pool address / market-data source")
        opened=float(row.get("opened_at") or 0)
        if opened<=0:
            raise HTTPException(400,"Campaign entry timestamp is unavailable")
        end=float(row.get("closed_at") or 0) or time.time()
        end=min(end,time.time())
        # Include a pre-entry warm-up window so range selection sees only data
        # that genuinely existed before the campaign began.
        start=max(0,opened-72*3600)
        try:
            resolved,pool=live.market.resolve_pool(str(row.get("chain") or "ROBINHOOD_CHAIN"),pool_address)
            ctx=snap.get("range_context") or {}
            human=str(ctx.get("human_price_asset") or "").upper()
            base_sym=str((pool.get("base_token") or {}).get("symbol") or "").upper()
            quote_sym=str((pool.get("quote_token") or {}).get("symbol") or "").upper()
            token_side="quote" if human and human==quote_sym else "base"
            candles=live.market.ohlcv_window(resolved,pool_address,int(start),int(end),token=token_side)
            if len(candles)<30:
                raise ValueError(f"Only {len(candles)} historical candles were returned for the campaign window")
            warmup=sum(1 for c in candles if float(c.get("timestamp") or 0)<opened)
            warmup=max(24,min(warmup,len(candles)-12))
            replay_pool=dict(pool)
            if token_side=="quote":
                original_base=pool.get("base_token") or {}; original_quote=pool.get("quote_token") or {}
                replay_pool["base_token"],replay_pool["quote_token"]=original_quote,original_base
                replay_pool["base_token_price_usd"]=pool.get("quote_token_price_usd")
                replay_pool["quote_token_price_usd"]=pool.get("base_token_price_usd")
                replay_pool["pair"]=f"{original_quote.get('symbol','?')}/{original_base.get('symbol','?')}"
                try:
                    token_meta=live.market.token(resolved,str(original_quote.get("address") or ""))
                    replay_pool["market_cap_usd"]=token_meta.get("market_cap_usd")
                    replay_pool["fdv_usd"]=token_meta.get("fdv_usd")
                except Exception:
                    replay_pool["market_cap_usd"]=None; replay_pool["fdv_usd"]=None
            result=run_replay(
                candles,sleeve=str(row.get("strategy_sleeve") or "TACTICAL_CAMPAIGN"),
                pair=str(replay_pool.get("pair") or row.get("pair") or "CAMPAIGN"),chain=resolved,protocol=str(row.get("protocol") or "UNISWAP_V3"),
                warmup_candles=warmup,initial_capital=max(1.0,float(row.get("capital_value") or 1)),
                inventory_intent=str(row.get("inventory_intent") or "BALANCED"),pool_context=replay_pool,target_monthly_pct=max(0.0,target_monthly_pct),
            )
            legacy=snap.get("legacy_campaign") or {}; current=snap.get("current") or legacy.get("current") or {}; entry=snap.get("entry") or legacy.get("entry") or {}
            result["campaign_comparison"]={
                "position_id":position_id,"display_name":row.get("display_name") or row.get("pair"),
                "actual_range":{"lower":row.get("lower_price"),"upper":row.get("upper_price"),"unit":row.get("range_unit")},
                "entry_nav_usd":entry.get("entry_nav_usd") or row.get("capital_value"),
                "last_lp_value_usd":current.get("total_lp_value_usd") or row.get("current_value"),
                "reported_net_pnl_usd":current.get("net_pnl_usd") if current.get("net_pnl_usd") is not None else row.get("reported_net_pnl"),
                "reported_net_pnl_pct":current.get("absolute_pnl_pct") if current.get("absolute_pnl_pct") is not None else row.get("reported_net_pnl_pct"),
                "unclaimed_fees_usd":current.get("unclaimed_fees_usd"),
                "lp_vs_hodl_net_usd":current.get("lp_vs_hodl_net_usd"),
                "pnl_status":current.get("absolute_pnl_status") or row.get("pnl_quality"),
                "entry_timestamp":opened,"observed_end_timestamp":end,
                "note":"Actual campaign fields come from the legacy ledger; replay economics remain modelled unless explicitly observed.",
            }
            saved=store.save_replay_run(result,scenario=f"LEGACY_{row.get('display_name') or position_id}")
            return {"saved":saved,"result":result}
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502,str(exc)) from exc

    @app.get("/api/positions/{position_id}/ai-review")
    def position_ai_review(position_id: str):
        row=store.get_position(position_id)
        if not row: raise HTTPException(404, "Position not found")
        return intelligence.position_review(row)

    @app.post("/api/positions/{position_id}/plan")
    def plan(position_id: str):
        row = store.get_position(position_id)
        if not row:
            raise HTTPException(404, "Position not found")
        decision = deterministic_plan(row)
        store.add_decision(decision)
        return {"decision": decision.to_dict(), "risk": edge_risk(row)}

    @app.post("/api/positions/{position_id}/prepare-close")
    def prepare_close(position_id: str):
        row = store.get_position(position_id)
        if not row:
            raise HTTPException(404, "Position not found")
        return executor.prepare_close(row)

    @app.post("/api/positions/{position_id}/prepare-collect")
    def prepare_collect(position_id: str):
        row = store.get_position(position_id)
        if not row:
            raise HTTPException(404, "Position not found")
        return executor.prepare_collect(row)

    @app.post("/api/open/prepare")
    def prepare_open(intent: OpenIntent):
        if intent.lower_price <= 0 or intent.upper_price <= intent.lower_price or intent.capital_value <= 0:
            raise HTTPException(400, "Invalid range or capital value")
        return executor.prepare_open(intent.model_dump())

    @app.post("/api/import/delta-history")
    def import_delta_history(payload: dict[str, Any]):
        result=import_delta_pool_history(store,payload)
        if not result.get("ok"): raise HTTPException(400,str(result.get("reason") or "Invalid history file"))
        return result

    @app.get("/api/execution/pool/{chain}/{address}")
    def execution_pool(chain: str, address: str):
        meta=read_v3_pool_metadata(chain.upper(),address)
        if not meta.get("ok"): raise HTTPException(502,str(meta.get("error") or "Pool metadata unavailable"))
        return meta

    @app.post("/api/execution/open/quote")
    def execution_open_quote(intent: ExecutionQuoteIntent):
        from .live_v3_builder import quote_open_position_amounts
        try:
            return quote_open_position_amounts(
                chain=intent.chain.upper(), pool_address=intent.pool_address,
                lower_price=intent.lower_price, upper_price=intent.upper_price,
                known_side=int(intent.known_side), known_amount=max(0.0,float(intent.known_amount)),
            )
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502,str(exc)) from exc

    @app.post("/api/execution/open/build")
    def execution_open_build(intent: ExecutionOpenIntent):
        from .live_v3_builder import build_open_position
        if not settings.wallet_address:
            raise HTTPException(400,"WALLET_ADDRESS is not configured")
        try:
            built=build_open_position(chain=intent.chain.upper(),pool_address=intent.pool_address,wallet=settings.wallet_address,lower_price=intent.lower_price,upper_price=intent.upper_price,amount0=intent.amount0,amount1=intent.amount1,slippage_bps=intent.slippage_bps,ttl_seconds=intent.ttl_seconds,wrap_native_amount=intent.wrap_native_amount)
            store.record_action(position_id=None,action_type="PREPARE_OPEN_LIVE",mode="manual_wallet",status=str((built.get("simulation") or {}).get("status") or "PREPARED"),payload=intent.model_dump(),result=built)
            return built
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502,str(exc)) from exc

    @app.get("/api/decisions/grouped")
    def decisions_grouped():
        rows=store.list_decisions(500); positions_by_id={str(p.get("id")):p for p in store.list_positions()}
        groups={}
        for d in rows:
            pid=str(d.get("position_id") or "")
            ev=d.get("evidence") or {}; pool=str(ev.get("pool") or ""); pair=str(ev.get("pair") or "")
            if pid:
                subject=f"position:{pid}"; pos=positions_by_id.get(pid) or {}; category="OPEN_POSITION" if str(pos.get("status") or "").upper()=="OPEN" else "CLOSED_POSITION"; sleeve=str(pos.get("strategy_sleeve") or "TACTICAL_CAMPAIGN")
            else:
                subject=f"opportunity:{pool or pair or d.get('trigger')}"; category="OPPORTUNITY"; sleeve=str(ev.get("sleeve") or "")
            g=groups.setdefault(subject,{"subject":subject,"category":category,"sleeve":sleeve,"latest":d,"history":[]})
            if g["latest"] is not d: g["history"].append(d)
        return sorted(groups.values(),key=lambda g:float((g.get("latest") or {}).get("created_at") or 0),reverse=True)

    @app.post("/api/import/legacy")
    def import_legacy():
        return import_campaign_ledger(store, settings.legacy_root)

    @app.get("/api/decisions")
    def decisions():
        return store.list_decisions(100)

    @app.get("/api/actions")
    def actions():
        return store.list_actions(100)

    return app


app = create_app()
