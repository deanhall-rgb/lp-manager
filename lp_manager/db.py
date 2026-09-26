from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import Decision, Position


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL,
    chain TEXT NOT NULL,
    pair TEXT NOT NULL,
    status TEXT NOT NULL,
    lower_price REAL NOT NULL,
    upper_price REAL NOT NULL,
    current_price REAL NOT NULL,
    capital_value REAL NOT NULL,
    current_value REAL NOT NULL,
    unclaimed_fees REAL NOT NULL DEFAULT 0,
    fees_today REAL NOT NULL DEFAULT 0,
    fees_7d REAL NOT NULL DEFAULT 0,
    fees_30d REAL NOT NULL DEFAULT 0,
    realised_fees REAL NOT NULL DEFAULT 0,
    estimated_il REAL NOT NULL DEFAULT 0,
    gas_costs REAL NOT NULL DEFAULT 0,
    apr_current REAL NOT NULL DEFAULT 0,
    apr_7d REAL NOT NULL DEFAULT 0,
    opened_at REAL NOT NULL,
    token_id TEXT,
    campaign_id TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT NOT NULL DEFAULT '',
    strategy_sleeve TEXT NOT NULL DEFAULT 'TACTICAL_CAMPAIGN',
    directional_bias TEXT NOT NULL DEFAULT 'NEUTRAL',
    inventory_intent TEXT NOT NULL DEFAULT 'BALANCED',
    target_hold_days REAL NOT NULL DEFAULT 3.0,
    monitoring_class TEXT NOT NULL DEFAULT 'ACTIVE',
    display_name TEXT NOT NULL DEFAULT '',
    campaign_label TEXT NOT NULL DEFAULT '',
    entry_thesis TEXT NOT NULL DEFAULT '',
    exit_goal TEXT NOT NULL DEFAULT '',
    lifecycle_stage TEXT NOT NULL DEFAULT 'ACTIVE',
    cost_basis_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
    strategy_version TEXT NOT NULL DEFAULT 'v0.8',
    pool_address TEXT NOT NULL DEFAULT '',
    range_unit TEXT NOT NULL DEFAULT 'TOKEN_PRICE_USD',
    closed_at REAL NOT NULL DEFAULT 0,
    reported_net_pnl REAL NOT NULL DEFAULT 0,
    reported_net_pnl_pct REAL NOT NULL DEFAULT 0,
    pnl_quality TEXT NOT NULL DEFAULT 'UNKNOWN'
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    position_id TEXT,
    created_at REAL NOT NULL,
    severity TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    source TEXT NOT NULL DEFAULT 'DETERMINISTIC',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    position_id TEXT,
    created_at REAL NOT NULL,
    action_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS replay_runs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    scenario TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    pair TEXT NOT NULL,
    chain TEXT NOT NULL,
    protocol TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcome_audits (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    verdict TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS position_snapshots (
    position_id TEXT PRIMARY KEY,
    updated_at REAL NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wallet_snapshots (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at REAL NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    protocol TEXT NOT NULL,
    pair TEXT NOT NULL,
    pool_address TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'WATCH',
    preferred_sleeve TEXT,
    preferred_score REAL NOT NULL DEFAULT 0,
    candidate_json TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_snapshots (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    position_id TEXT,
    chain TEXT NOT NULL,
    pool_address TEXT NOT NULL,
    pair TEXT NOT NULL,
    sleeve TEXT NOT NULL,
    horizon_days REAL NOT NULL,
    capital_usd REAL NOT NULL,
    lower_price REAL NOT NULL,
    upper_price REAL NOT NULL,
    spot REAL NOT NULL,
    expected_fees_usd REAL NOT NULL,
    expected_net_usd REAL NOT NULL,
    forecast_fee_apr_pct REAL NOT NULL,
    low_net_usd REAL NOT NULL,
    high_net_usd REAL NOT NULL,
    model_version TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS financial_events (
    id TEXT PRIMARY KEY,
    position_id TEXT,
    created_at REAL NOT NULL,
    occurred_at REAL NOT NULL,
    event_type TEXT NOT NULL,
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL DEFAULT '',
    amount_usd REAL NOT NULL DEFAULT 0,
    gas_native REAL NOT NULL DEFAULT 0,
    gas_usd REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'CONFIRMED',
    payload_json TEXT NOT NULL DEFAULT '{}'
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            self._migrate_positions(con)
            self._migrate_decisions(con)

    @staticmethod
    def _migrate_positions(con: sqlite3.Connection) -> None:
        existing = {row[1] for row in con.execute("PRAGMA table_info(positions)").fetchall()}
        additions = {
            "strategy_sleeve": "TEXT NOT NULL DEFAULT 'TACTICAL_CAMPAIGN'",
            "directional_bias": "TEXT NOT NULL DEFAULT 'NEUTRAL'",
            "inventory_intent": "TEXT NOT NULL DEFAULT 'BALANCED'",
            "target_hold_days": "REAL NOT NULL DEFAULT 3.0",
            "monitoring_class": "TEXT NOT NULL DEFAULT 'ACTIVE'",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "campaign_label": "TEXT NOT NULL DEFAULT ''",
            "entry_thesis": "TEXT NOT NULL DEFAULT ''",
            "exit_goal": "TEXT NOT NULL DEFAULT ''",
            "lifecycle_stage": "TEXT NOT NULL DEFAULT 'ACTIVE'",
            "cost_basis_quality": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "strategy_version": "TEXT NOT NULL DEFAULT 'v0.8'",
            "pool_address": "TEXT NOT NULL DEFAULT ''",
            "range_unit": "TEXT NOT NULL DEFAULT 'TOKEN_PRICE_USD'",
            "closed_at": "REAL NOT NULL DEFAULT 0",
            "reported_net_pnl": "REAL NOT NULL DEFAULT 0",
            "reported_net_pnl_pct": "REAL NOT NULL DEFAULT 0",
            "pnl_quality": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        }
        for name, ddl in additions.items():
            if name not in existing:
                con.execute(f"ALTER TABLE positions ADD COLUMN {name} {ddl}")

    @staticmethod
    def _migrate_decisions(con: sqlite3.Connection) -> None:
        existing = {row[1] for row in con.execute("PRAGMA table_info(decisions)").fetchall()}
        additions = {
            "source": "TEXT NOT NULL DEFAULT 'DETERMINISTIC'",
            "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, ddl in additions.items():
            if name not in existing:
                con.execute(f"ALTER TABLE decisions ADD COLUMN {name} {ddl}")

    def upsert_position(self, position: Position) -> None:
        data = position.to_dict()
        cols = list(data)
        marks = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id")
        sql = f"INSERT INTO positions ({','.join(cols)}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}"
        with self.connect() as con:
            con.execute(sql, [data[c] for c in cols])

    def list_positions(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            if status:
                rows = con.execute("SELECT * FROM positions WHERE status=? ORDER BY opened_at DESC", (status,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM positions ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, opened_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_position(self, position_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return dict(row) if row else None

    def close_position_record(self, position_id: str) -> bool:
        with self.connect() as con:
            cur = con.execute("UPDATE positions SET status='CLOSED' WHERE id=?", (position_id,))
        return bool(cur.rowcount)

    def add_decision(self, decision: Decision) -> None:
        d = decision.to_dict()
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO decisions(id,position_id,created_at,severity,action,confidence,summary,rationale,trigger,status,source,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["id"],d["position_id"],d["created_at"],d["severity"],d["action"],d["confidence"],d["summary"],d["rationale"],d["trigger"],d["status"],d.get("source") or "DETERMINISTIC",json.dumps(d.get("evidence") or {},sort_keys=True)),
            )

    def list_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r)
            if "evidence_json" in row:
                try: row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
                except Exception: row["evidence"] = {}
            out.append(row)
        return out

    def record_action(self, *, position_id: str | None, action_type: str, mode: str, status: str, payload: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4().hex,
            "position_id": position_id,
            "created_at": time.time(),
            "action_type": action_type,
            "mode": mode,
            "status": status,
            "payload_json": json.dumps(payload, sort_keys=True),
            "result_json": json.dumps(result or {}, sort_keys=True),
        }
        with self.connect() as con:
            con.execute(
                "INSERT INTO actions(id,position_id,created_at,action_type,mode,status,payload_json,result_json) VALUES (?,?,?,?,?,?,?,?)",
                tuple(row[k] for k in ("id","position_id","created_at","action_type","mode","status","payload_json","result_json")),
            )
        return row

    def list_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM actions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
            row["result"] = json.loads(row.pop("result_json") or "{}")
            out.append(row)
        return out

    def upsert_opportunity(self, *, candidate: dict[str, Any], evaluation: dict[str, Any], status: str = "WATCH") -> dict[str, Any]:
        now = time.time()
        pool = str(candidate.get("pool_address") or "").lower()
        key = pool or f"{candidate.get('chain')}|{candidate.get('protocol')}|{candidate.get('pair')}"
        opp_id = uuid.uuid5(uuid.NAMESPACE_URL, key).hex
        with self.connect() as con:
            existing = con.execute("SELECT first_seen_at FROM opportunities WHERE id=?", (opp_id,)).fetchone()
            first_seen = float(existing[0]) if existing else now
            con.execute(
                """INSERT INTO opportunities(id,chain,protocol,pair,pool_address,first_seen_at,last_seen_at,status,preferred_sleeve,preferred_score,candidate_json,evaluation_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at,status=excluded.status,preferred_sleeve=excluded.preferred_sleeve,preferred_score=excluded.preferred_score,candidate_json=excluded.candidate_json,evaluation_json=excluded.evaluation_json""",
                (opp_id, str(candidate.get("chain") or ""), str(candidate.get("protocol") or ""), str(candidate.get("pair") or ""), candidate.get("pool_address"), first_seen, now, status, evaluation.get("preferred_sleeve"), float(evaluation.get("preferred_score") or 0.0), json.dumps(candidate, sort_keys=True), json.dumps(evaluation, sort_keys=True)),
            )
        return {"id": opp_id, "first_seen_at": first_seen, "last_seen_at": now, "status": status, "preferred_sleeve": evaluation.get("preferred_sleeve"), "preferred_score": evaluation.get("preferred_score")}

    def list_opportunities(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM opportunities ORDER BY preferred_score DESC,last_seen_at DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r)
            row["candidate"] = json.loads(row.pop("candidate_json") or "{}")
            row["evaluation"] = json.loads(row.pop("evaluation_json") or "{}")
            out.append(row)
        return out

    def get_opportunity(self, opportunity_id: str) -> dict[str, Any] | None:
        rows = [r for r in self.list_opportunities(1000) if r["id"] == opportunity_id]
        return rows[0] if rows else None

    def set_opportunity_status(self, opportunity_id: str, status: str) -> dict[str, Any] | None:
        with self.connect() as con:
            cur = con.execute("UPDATE opportunities SET status=?,last_seen_at=? WHERE id=?", (status, time.time(), opportunity_id))
        if not cur.rowcount:
            return None
        return self.get_opportunity(opportunity_id)

    def save_replay_run(self, result: dict[str, Any], *, scenario: str = "CUSTOM") -> dict[str, Any]:
        row = {
            "id": str(result.get("id") or uuid.uuid4().hex),
            "created_at": float(result.get("created_at") or time.time()),
            "scenario": str(scenario or "CUSTOM"),
            "sleeve": str(result.get("sleeve") or ""),
            "pair": str(result.get("pair") or ""),
            "chain": str(result.get("chain") or ""),
            "protocol": str(result.get("protocol") or ""),
            "summary_json": json.dumps(result.get("summary") or {}, sort_keys=True),
            "result_json": json.dumps(result, sort_keys=True),
        }
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO replay_runs(id,created_at,scenario,sleeve,pair,chain,protocol,summary_json,result_json) VALUES (?,?,?,?,?,?,?,?,?)",
                tuple(row[k] for k in ("id","created_at","scenario","sleeve","pair","chain","protocol","summary_json","result_json")),
            )
        return {k: row[k] for k in ("id","created_at","scenario","sleeve","pair","chain","protocol")} | {"summary": result.get("summary") or {}}

    def list_replay_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT id,created_at,scenario,sleeve,pair,chain,protocol,summary_json FROM replay_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r); row["summary"]=json.loads(row.pop("summary_json") or "{}")
            out.append(row)
        return out

    def get_replay_run(self, replay_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row=con.execute("SELECT result_json FROM replay_runs WHERE id=?", (replay_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_outcome_audit(self, *, subject_type: str, subject_id: str, horizon: str, verdict: str, payload: dict[str, Any]) -> dict[str, Any]:
        row={"id":uuid.uuid4().hex,"created_at":time.time(),"subject_type":subject_type,"subject_id":subject_id,"horizon":horizon,"verdict":verdict,"payload_json":json.dumps(payload,sort_keys=True)}
        with self.connect() as con:
            con.execute("INSERT INTO outcome_audits(id,created_at,subject_type,subject_id,horizon,verdict,payload_json) VALUES (?,?,?,?,?,?,?)", tuple(row[k] for k in ("id","created_at","subject_type","subject_id","horizon","verdict","payload_json")))
        return {**row, "payload": payload}

    def list_outcome_audits(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows=con.execute("SELECT * FROM outcome_audits ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r); row["payload"]=json.loads(row.pop("payload_json") or "{}"); out.append(row)
        return out

    def record_forecast_snapshot(self, result: dict[str, Any], *, model_version: str = "v0.8.11") -> dict[str, Any]:
        best=dict(result.get("recommended_range") or {})
        forecast=dict(best.get("forecast") or {})
        row={
            "id":uuid.uuid4().hex,
            "created_at":time.time(),
            "position_id":None,
            "chain":str(result.get("chain") or ""),
            "pool_address":str(result.get("pool_address") or ""),
            "pair":str(result.get("pair") or ""),
            "sleeve":str(result.get("sleeve") or ""),
            "horizon_days":float(result.get("horizon_days") or 0),
            "capital_usd":float(result.get("capital_usd") or 0),
            "lower_price":float(best.get("lower") or 0),
            "upper_price":float(best.get("upper") or 0),
            "spot":float(result.get("spot") or 0),
            "expected_fees_usd":float(forecast.get("expected_fees_usd") or 0),
            "expected_net_usd":float(forecast.get("expected_net_usd") or 0),
            "forecast_fee_apr_pct":float(forecast.get("forecast_fee_apr_pct") or 0),
            "low_net_usd":float(forecast.get("low_net_usd") or 0),
            "high_net_usd":float(forecast.get("high_net_usd") or 0),
            "model_version":str(model_version or "v0.8.11"),
            "payload_json":json.dumps(result,sort_keys=True),
        }
        with self.connect() as con:
            con.execute(
                """INSERT INTO forecast_snapshots(
                    id,created_at,position_id,chain,pool_address,pair,sleeve,horizon_days,capital_usd,
                    lower_price,upper_price,spot,expected_fees_usd,expected_net_usd,forecast_fee_apr_pct,
                    low_net_usd,high_net_usd,model_version,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(row[k] for k in (
                    "id","created_at","position_id","chain","pool_address","pair","sleeve","horizon_days",
                    "capital_usd","lower_price","upper_price","spot","expected_fees_usd","expected_net_usd",
                    "forecast_fee_apr_pct","low_net_usd","high_net_usd","model_version","payload_json"
                )),
            )
        return {k:v for k,v in row.items() if k!="payload_json"}

    def list_forecast_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows=con.execute("SELECT * FROM forecast_snapshots ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r)
            try: row["payload"]=json.loads(row.pop("payload_json") or "{}")
            except Exception: row["payload"]={}
            out.append(row)
        return out

    def latest_forecast_for_position(self, position_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row=con.execute(
                "SELECT * FROM forecast_snapshots WHERE position_id=? ORDER BY created_at DESC LIMIT 1",
                (str(position_id),),
            ).fetchone()
        if not row:
            return None
        out=dict(row)
        try:
            out["payload"]=json.loads(out.pop("payload_json") or "{}")
        except Exception:
            out["payload"]={}
        return out

    def link_forecast_to_position(self, forecast_id: str, position_id: str) -> bool:
        with self.connect() as con:
            cur=con.execute("UPDATE forecast_snapshots SET position_id=? WHERE id=?",(position_id,forecast_id))
        return bool(cur.rowcount)

    def record_financial_event(
        self, *, position_id: str | None, event_type: str, chain: str, tx_hash: str = "",
        occurred_at: float | None = None, amount_usd: float = 0.0, gas_native: float = 0.0,
        gas_usd: float = 0.0, status: str = "CONFIRMED", payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row={
            "id":uuid.uuid4().hex,"position_id":position_id,"created_at":time.time(),
            "occurred_at":float(occurred_at or time.time()),"event_type":str(event_type or ""),
            "chain":str(chain or ""),"tx_hash":str(tx_hash or ""),"amount_usd":float(amount_usd or 0),
            "gas_native":float(gas_native or 0),"gas_usd":float(gas_usd or 0),
            "status":str(status or "CONFIRMED"),"payload_json":json.dumps(payload or {},sort_keys=True),
        }
        with self.connect() as con:
            con.execute(
                """INSERT INTO financial_events(
                    id,position_id,created_at,occurred_at,event_type,chain,tx_hash,amount_usd,
                    gas_native,gas_usd,status,payload_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(row[k] for k in (
                    "id","position_id","created_at","occurred_at","event_type","chain","tx_hash",
                    "amount_usd","gas_native","gas_usd","status","payload_json"
                )),
            )
        return {**{k:v for k,v in row.items() if k!="payload_json"},"payload":payload or {}}

    def list_financial_events(self, limit: int = 500, position_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as con:
            if position_id:
                rows=con.execute(
                    "SELECT * FROM financial_events WHERE position_id=? ORDER BY occurred_at DESC LIMIT ?",
                    (position_id,limit),
                ).fetchall()
            else:
                rows=con.execute("SELECT * FROM financial_events ORDER BY occurred_at DESC LIMIT ?",(limit,)).fetchall()
        out=[]
        for r in rows:
            row=dict(r)
            try: row["payload"]=json.loads(row.pop("payload_json") or "{}")
            except Exception: row["payload"]={}
            out.append(row)
        return out

    def reconcile_execution_opening(self, tx_hash: str, position_id: str) -> dict[str, Any]:
        """Attach a confirmed browser-wallet open event/forecast to its discovered NFT."""
        tx=str(tx_hash or "").lower()
        if not tx or not position_id:
            return {"linked_events":0,"linked_forecasts":0}
        linked_events=linked_forecasts=0
        with self.connect() as con:
            rows=con.execute(
                "SELECT id,payload_json FROM financial_events WHERE lower(tx_hash)=? AND event_type='OPEN_POSITION'",
                (tx,),
            ).fetchall()
            for row in rows:
                con.execute("UPDATE financial_events SET position_id=? WHERE id=?",(position_id,str(row[0])))
                linked_events+=1
                try:
                    payload=json.loads(row[1] or "{}")
                except Exception:
                    payload={}
                forecast_id=str(payload.get("forecast_id") or "")
                if forecast_id:
                    cur=con.execute("UPDATE forecast_snapshots SET position_id=? WHERE id=?",(position_id,forecast_id))
                    linked_forecasts+=int(cur.rowcount or 0)
            if linked_events:
                event_gas=con.execute(
                    """SELECT COALESCE(SUM(gas_usd),0) FROM financial_events
                       WHERE position_id=? AND upper(status)='CONFIRMED'""",
                    (position_id,),
                ).fetchone()[0]
                existing=con.execute("SELECT gas_costs FROM positions WHERE id=?",(position_id,)).fetchone()
                if existing:
                    con.execute(
                        "UPDATE positions SET gas_costs=? WHERE id=?",
                        (max(float(existing[0] or 0),float(event_gas or 0)),position_id),
                    )
        return {"linked_events":linked_events,"linked_forecasts":linked_forecasts}


    def add_position_gas_cost(self, position_id: str, gas_usd: float) -> float:
        value=max(0.0,float(gas_usd or 0))
        with self.connect() as con:
            con.execute("UPDATE positions SET gas_costs=COALESCE(gas_costs,0)+? WHERE id=?",(value,position_id))
            row=con.execute("SELECT gas_costs FROM positions WHERE id=?",(position_id,)).fetchone()
        return float(row[0] or 0) if row else 0.0


    def record_closed_position_economics(
        self, position_id: str, *, reported_net_pnl: float, reported_net_pnl_pct: float,
        gas_costs: float, quality: str = "ONCHAIN_CLOSE_RECEIPT",
    ) -> bool:
        with self.connect() as con:
            cur=con.execute(
                """UPDATE positions SET
                    reported_net_pnl=?,reported_net_pnl_pct=?,gas_costs=?,
                    pnl_quality=?,status='CLOSED',lifecycle_stage='CLOSED',
                    closed_at=CASE WHEN closed_at>0 THEN closed_at ELSE ? END
                   WHERE id=?""",
                (
                    float(reported_net_pnl),float(reported_net_pnl_pct),float(gas_costs),
                    str(quality or "ONCHAIN_CLOSE_RECEIPT"),time.time(),position_id,
                ),
            )
        return bool(cur.rowcount)


    def finalize_closed_position(
        self, position_id: str, *, opening_capital_usd: float, total_fees_usd: float,
        gas_costs_usd: float, realised_pnl_usd: float, realised_return_pct: float,
        closed_at: float, quality: str,
    ) -> bool:
        """Freeze a completed closed-position lifecycle into the portfolio ledger."""
        with self.connect() as con:
            cur=con.execute(
                """UPDATE positions SET
                    status='CLOSED',lifecycle_stage='CLOSED_FINAL',
                    capital_value=?,current_value=0,unclaimed_fees=0,
                    realised_fees=?,gas_costs=?,reported_net_pnl=?,reported_net_pnl_pct=?,
                    pnl_quality=?,closed_at=?,strategy_version='v0.8.11'
                   WHERE id=?""",
                (
                    float(opening_capital_usd),float(total_fees_usd),float(gas_costs_usd),
                    float(realised_pnl_usd),float(realised_return_pct),
                    str(quality or "ONCHAIN_LIFECYCLE_FINAL"),float(closed_at or time.time()),
                    position_id,
                ),
            )
        return bool(cur.rowcount)


    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO settings(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (key, json.dumps(value, sort_keys=True), time.time()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as con:
            row = con.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return default


    def find_position_by_token(self, chain: str, token_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM positions WHERE chain=? AND token_id=? ORDER BY CASE source WHEN 'live_chain' THEN 0 WHEN 'historical_pool_reconstruction' THEN 1 WHEN 'legacy_campaign_ledger' THEN 2 ELSE 3 END LIMIT 1", (chain, str(token_id))).fetchone()
        return dict(row) if row else None

    def update_position_metadata(self, position_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"display_name","campaign_label","entry_thesis","exit_goal","lifecycle_stage","cost_basis_quality","strategy_version","strategy_sleeve","directional_bias","inventory_intent","target_hold_days","monitoring_class","notes"}
        payload = {k:v for k,v in fields.items() if k in allowed}
        if not payload:
            return self.get_position(position_id)
        sql = "UPDATE positions SET " + ",".join(f"{k}=?" for k in payload) + " WHERE id=?"
        with self.connect() as con:
            cur=con.execute(sql, [*payload.values(), position_id])
        return self.get_position(position_id) if cur.rowcount else None

    def save_wallet_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO wallet_snapshots(id,updated_at,snapshot_json) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,snapshot_json=excluded.snapshot_json", (time.time(), json.dumps(snapshot,sort_keys=True)))

    def get_wallet_snapshot(self) -> dict[str, Any] | None:
        with self.connect() as con:
            row=con.execute("SELECT updated_at,snapshot_json FROM wallet_snapshots WHERE id=1").fetchone()
        if not row: return None
        payload=json.loads(row[1] or "{}")
        payload["stored_at"]=float(row[0])
        return payload

    def save_position_snapshot(self, position_id: str, snapshot: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO position_snapshots(position_id,updated_at,snapshot_json) VALUES (?,?,?) ON CONFLICT(position_id) DO UPDATE SET updated_at=excluded.updated_at,snapshot_json=excluded.snapshot_json",
                (position_id, time.time(), json.dumps(snapshot, sort_keys=True)),
            )

    def get_position_snapshot(self, position_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT updated_at,snapshot_json FROM position_snapshots WHERE position_id=?", (position_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[1] or "{}")
        payload["stored_at"] = float(row[0])
        return payload

    def live_token_ids(self, chain: str) -> set[int]:
        """NFTs that belong in the current live refresh loop.

        Historical CLOSED positions are intentionally excluded. Once an OPEN NFT is
        observed closed, its accounting/history is handled separately and must never
        block current fee/value/range refreshes for positions that are still open.
        """
        with self.connect() as con:
            rows = con.execute(
                "SELECT token_id FROM positions WHERE chain=? AND token_id IS NOT NULL AND status='OPEN'",
                (chain,),
            ).fetchall()
        out=set()
        for row in rows:
            try: out.add(int(row[0]))
            except Exception: pass
        return out

    def close_missing_live_positions(self, chain: str, seen_ids: set[str]) -> int:
        with self.connect() as con:
            rows = con.execute("SELECT id FROM positions WHERE source='live_chain' AND chain=? AND status='OPEN'", (chain,)).fetchall()
            missing = [str(r[0]) for r in rows if str(r[0]) not in seen_ids]
            for pid in missing:
                con.execute("UPDATE positions SET status='CLOSED',notes=notes || ? WHERE id=?", ("\nClosed by live reconciliation: NFT no longer owned by configured wallet.", pid))
        return len(missing)

    def archive_superseded_legacy_delta(self, authoritative_token_ids: set[str]) -> int:
        """Keep old campaign-ledger rows for audit but remove them from active product views.

        The legacy bot auto-numbered DELTA records independently of the later
        pool reconstruction, so calling them LP1/LP2/LP3 creates false identity.
        Once authoritative research history is imported, unmatched legacy rows
        are marked archived rather than deleted.
        """
        keep={str(x) for x in authoritative_token_ids if str(x)}
        with self.connect() as con:
            rows=con.execute("SELECT id,token_id,display_name FROM positions WHERE source='legacy_campaign_ledger' AND upper(pair) LIKE '%DELTA%'").fetchall()
            changed=0
            for row in rows:
                token=str(row[1] or '')
                if token and token in keep:
                    continue
                name=str(row[2] or 'Legacy DELTA campaign')
                if not name.startswith('Legacy '):
                    name='Legacy '+name
                con.execute("UPDATE positions SET monitoring_class='ARCHIVED_SUPERSEDED',display_name=?,campaign_label='LEGACY_ARCHIVE',notes=notes || ? WHERE id=?",(name,"\nSuperseded in product views by imported DELTA pool-history reconstruction.",str(row[0])))
                changed+=1
        return changed

    def purge_demo_positions(self) -> int:
        with self.connect() as con:
            rows = con.execute("SELECT id FROM positions WHERE source='demo'").fetchall()
            ids = [str(r[0]) for r in rows]
            for pid in ids:
                con.execute("DELETE FROM position_snapshots WHERE position_id=?", (pid,))
            cur = con.execute("DELETE FROM positions WHERE source='demo'")
        return int(cur.rowcount or 0)

    def count_positions(self) -> int:
        with self.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
