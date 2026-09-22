from __future__ import annotations

import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .allocation import AllocationLimits, suggest_allocation
from .analytics import enrich_position, portfolio_by_sleeve, portfolio_summary
from .compounding import fee_action
from .config import load_settings
from .db import Store
from .event_engine import detect_material_event, review_due
from .execution import ExecutionService
from .legacy_bridge import discover_legacy_sources, import_campaign_ledger
from .live_service import LiveDataService
from .live_scout import preliminary_pool_evaluation
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


class PoolReplayIntent(BaseModel):
    days: int = 30
    sleeve: str = "CORE_INCOME"
    initial_capital: float = 1000.0


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


def create_app(project_root: Path | None = None) -> FastAPI:
    settings = load_settings(project_root)
    store = Store(settings.database_path)
    if settings.demo_seed:
        seed_demo(store)
    else:
        store.purge_demo_positions()
    executor = ExecutionService(settings, store)
    live = LiveDataService(settings, store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        live.start_background()
        try:
            yield
        finally:
            live.stop_background()

    app = FastAPI(title="LP Manager", version="0.5.0-live", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "version": "0.5.0-live",
            "server_time": time.time(),
            "database": str(settings.database_path),
            "execution": executor.capabilities(),
            "live": live.status(),
        }

    @app.get("/api/overview")
    def overview():
        positions = store.list_positions()
        enriched = []
        for p in positions:
            row = enrich_position(p)
            snap = store.get_position_snapshot(str(p.get("id")))
            if snap:
                row["live_snapshot"] = snap
            enriched.append(row)
        open_positions = [p for p in enriched if p["status"] == "OPEN"]
        attention = sorted(
            [{"position": p, "risk": edge_risk(p)} for p in open_positions],
            key=lambda row: row["risk"]["score"], reverse=True,
        )
        return {
            "summary": portfolio_summary(positions),
            "portfolio_by_sleeve": portfolio_by_sleeve(positions),
            "strategy_profiles": policies_payload(),
            "scout_universe": scout_universe(),
            "opportunities": store.list_opportunities(20),
            "replay_runs": store.list_replay_runs(12),
            "outcome_audits": store.list_outcome_audits(12),
            "positions": enriched,
            "attention": attention,
            "decisions": store.list_decisions(12),
            "actions": store.list_actions(12),
            "execution": executor.capabilities(),
            "legacy_sources": discover_legacy_sources(settings.legacy_root),
            "live": live.status(),
        }

    @app.get("/api/live/status")
    def live_status():
        return live.status()

    @app.post("/api/live/refresh")
    def live_refresh(intent: LiveRefreshIntent = LiveRefreshIntent()):
        return live.refresh_positions(intent.chains)

    @app.get("/api/scout/live/{chain}")
    def live_scout(chain: str, limit: int = 20):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            rows = live.market.network_pools(chain.upper())
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        out=[]
        for row in rows:
            if str(row.get("protocol") or "").upper() != "UNISWAP_V3":
                continue
            evaluation = preliminary_pool_evaluation(row)
            out.append({**row, "evaluation": evaluation})
        out.sort(key=lambda r: float(r.get("tvl_usd") or 0), reverse=True)
        return {"chain": chain.upper(), "count": min(len(out), max(1, min(100, limit))), "pools": out[:max(1,min(100,limit))]}

    @app.get("/api/scout/pools/{chain}/{address}")
    def live_pool_detail(chain: str, address: str):
        if not live.market:
            raise HTTPException(503, "GeckoTerminal market data is disabled")
        try:
            row = live.market.pool(chain.upper(), address)
            row["evaluation"] = preliminary_pool_evaluation(row)
            row["ohlcv_7d"] = live.market.ohlcv_days(chain.upper(), address, 7)
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
            pool = live.market.pool(chain.upper(), address)
            candles = live.market.ohlcv_days(chain.upper(), address, max(3, min(365, intent.days)))
            if len(candles) < 30:
                raise ValueError(f"Only {len(candles)} historical candles were returned")
            warmup = min(max(24, len(candles)//4), max(24, len(candles)-24))
            result = run_replay(candles, sleeve=intent.sleeve, pair=str(pool.get("pair") or "POOL"), chain=chain.upper(), protocol=str(pool.get("protocol") or "UNISWAP_V3"), warmup_candles=warmup, initial_capital=intent.initial_capital)
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
                pair = "DELTA/WETH"
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
            extra={"version":"0.5.0-live", "execution":executor.capabilities(), "scout_universe":scout_universe()},
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
            positions=store.list_positions("OPEN"),
            opportunity=intent.opportunity,
            limits=limits,
        )

    @app.post("/api/targets/calculate")
    def targets_calculate(intent: TargetIntent):
        return performance_targets(**intent.model_dump())

    @app.get("/api/positions")
    def positions():
        out=[]
        for p in store.list_positions():
            row=enrich_position(p); snap=store.get_position_snapshot(str(p.get("id")))
            if snap: row["live_snapshot"]=snap
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
        return payload

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
