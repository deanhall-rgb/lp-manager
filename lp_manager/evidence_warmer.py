from __future__ import annotations

import threading
import time
from typing import Any

from .chain_registry import CHAINS
from .profit_engine import profit_history_cache_info, warm_profit_history


class EvidenceWarmService:
    """Small background worker that pre-warms Profit Lab execution-price history.

    It never runs range optimisation and never allocates capital. Durable history is
    written through the existing Profit Lab cache. Provider traffic is executed in
    the coordinator's background priority so interactive UI work always wins.
    """

    def __init__(self, store, market, candidate_universe, provider_coordinator=None):
        self.store=store
        self.market=market
        self.candidate_universe=candidate_universe
        self.coordinator=provider_coordinator
        self._stop=threading.Event()
        self._kick=threading.Event()
        self._lock=threading.Lock()
        self._thread: threading.Thread | None=None
        self._queued: dict[str,dict[str,Any]]={}

    @staticmethod
    def _key(row: dict[str,Any]) -> str:
        chain=str(row.get("chain") or "").upper()
        address=str(row.get("pool_address") or "").lower()
        return f"{chain}:{address}"

    def _hydrate(self, row: dict[str,Any]) -> dict[str,Any]:
        chain=str(row.get("chain") or "").upper()
        address=str(row.get("pool_address") or "").lower()
        if not chain or not address:
            return row
        try:
            for op in self.store.list_opportunities(500):
                candidate=dict(op.get("candidate") or {})
                if (
                    str(candidate.get("chain") or op.get("chain") or "").upper()==chain
                    and str(candidate.get("pool_address") or op.get("pool_address") or "").lower()==address
                ):
                    return {**candidate,**row,"chain":chain,"pool_address":address}
        except Exception:
            pass
        try:
            snap=self.candidate_universe.cached(chain)
            for candidate in snap.get("shortlist") or []:
                if str(candidate.get("pool_address") or "").lower()==address:
                    return {**candidate,**row,"chain":chain,"pool_address":address}
        except Exception:
            pass
        return {**row,"chain":chain,"pool_address":address}

    def enqueue(self, rows: list[dict[str,Any]] | dict[str,Any] | None) -> int:
        if not rows:
            return 0
        if isinstance(rows,dict):
            rows=[rows]
        added=0
        with self._lock:
            for raw in rows:
                row=self._hydrate(dict(raw or {}))
                key=self._key(row)
                if key.endswith(":") or key.startswith(":"):
                    continue
                if key not in self._queued:
                    added+=1
                self._queued[key]=row
        if added:
            self._kick.set()
        return added

    def _persistent_targets(self) -> list[dict[str,Any]]:
        rows: list[dict[str,Any]]=[]
        # Open positions first: these are the pools most likely to be inspected or
        # re-ranged, and warming their evidence is never speculative discovery.
        try:
            for pos in self.store.list_positions("OPEN"):
                if pos.get("pool_address"):
                    rows.append({
                        "chain":str(pos.get("chain") or "").upper(),
                        "pool_address":str(pos.get("pool_address") or ""),
                        "pair":pos.get("pair"),
                        "priority_reason":"OPEN_POSITION",
                    })
        except Exception:
            pass

        # Then current research-ready shortlist rows from every supported chain.
        for chain in CHAINS:
            try:
                snap=self.candidate_universe.cached(chain)
            except Exception:
                continue
            shortlist=list(snap.get("shortlist") or [])
            shortlist.sort(
                key=lambda x:(
                    bool(x.get("portfolio_overlap")),
                    bool(x.get("research_ready")),
                    float(x.get("discovery_score") or 0),
                ),
                reverse=True,
            )
            for candidate in shortlist[:5]:
                if candidate.get("pool_address") and str(candidate.get("protocol") or "").upper()=="UNISWAP_V3":
                    rows.append({**candidate,"priority_reason":"CANDIDATE_UNIVERSE"})

        # Persisted opportunities cover Advisor results even when a universe snapshot
        # is later replaced by another chain scan.
        try:
            for op in self.store.list_opportunities(80):
                candidate=dict(op.get("candidate") or {})
                if candidate.get("pool_address"):
                    rows.append({**candidate,"priority_reason":"PERSISTED_OPPORTUNITY"})
        except Exception:
            pass

        dedup={}
        for row in rows:
            key=self._key(row)
            if key.startswith(":") or key.endswith(":"):
                continue
            dedup.setdefault(key,self._hydrate(row))
        return list(dedup.values())

    def _next_targets(self, limit: int) -> list[dict[str,Any]]:
        limit=max(1,min(6,int(limit)))
        out=[]
        with self._lock:
            queued=list(self._queued.values())
            self._queued.clear()
        for row in queued:
            key=self._key(row)
            if key and all(self._key(x)!=key for x in out):
                out.append(row)
            if len(out)>=limit:
                return out

        persistent=self._persistent_targets()
        if not persistent:
            return out
        cursor=int(self.store.get_setting("evidence_warmer:cursor",0) or 0) % len(persistent)
        ordered=persistent[cursor:]+persistent[:cursor]
        for row in ordered:
            key=self._key(row)
            if key and all(self._key(x)!=key for x in out):
                out.append(row)
            if len(out)>=limit:
                break
        self.store.set_setting(
            "evidence_warmer:cursor",
            (cursor+max(1,len(out))) % max(1,len(persistent)),
        )
        return out

    def _persist_pool_status(self, row: dict[str,Any], result: dict[str,Any]) -> None:
        chain=str(row.get("chain") or result.get("chain") or "").upper()
        address=str(row.get("pool_address") or result.get("pool_address") or "").lower()
        if not chain or not address:
            return
        payload={
            **dict(result or {}),
            "chain":chain,
            "pool_address":address,
            "pair":row.get("pair") or result.get("pair"),
            "checked_at":time.time(),
        }
        try:
            self.store.set_setting(f"profit:evidence:status:{chain}:{address}",payload)
        except Exception:
            pass

    def run_once(self, *, max_items: int = 2) -> dict[str,Any]:
        started=time.time()
        rows=self._next_targets(max_items)
        results=[]
        for row in rows:
            chain=str(row.get("chain") or "").upper()
            address=str(row.get("pool_address") or "")
            if not chain or not address:
                continue
            info=profit_history_cache_info(self.store,chain,address,history_days=30)
            if info.get("fresh") and int(info.get("samples") or 0)>=24:
                item={
                    **info,"ok":True,"status":"CACHE_READY","warmed":False,
                    "pair":row.get("pair"),"priority_reason":row.get("priority_reason"),
                }
                results.append(item)
                self._persist_pool_status(row,item)
                continue
            try:
                if self.coordinator is not None:
                    with self.coordinator.background_work():
                        result=warm_profit_history(
                            self.market,self.store,chain,address,
                            history_days=30,pool_fallback=row,
                        )
                else:
                    result=warm_profit_history(
                        self.market,self.store,chain,address,
                        history_days=30,pool_fallback=row,
                    )
            except Exception as exc:
                result={"ok":False,"status":"FAILED","error":str(exc)[:300]}
            item={
                **result,
                "chain":chain,"pool_address":address,
                "pair":row.get("pair"),
                "priority_reason":row.get("priority_reason"),
            }
            results.append(item)
            self._persist_pool_status(row,item)

        payload={
            "ok":all(bool(x.get("ok")) for x in results) if results else True,
            "last_run_at":time.time(),
            "elapsed_ms":round((time.time()-started)*1000,1),
            "processed":len(results),
            "ready":sum(1 for x in results if x.get("ok")),
            "warmed":sum(1 for x in results if x.get("warmed")),
            "failed":sum(1 for x in results if not x.get("ok")),
            "queued":len(self._queued),
            "results":results[-6:],
        }
        try:
            self.store.set_setting("evidence_warmer:status",payload)
        except Exception:
            pass
        return payload

    def status(self) -> dict[str,Any]:
        payload=self.store.get_setting("evidence_warmer:status",{}) or {}
        with self._lock:
            queued=len(self._queued)
        return {
            **payload,
            "enabled":bool(self.market),
            "background_refresh":bool(self._thread and self._thread.is_alive()),
            "queued":queued,
        }

    def kick(self) -> None:
        self._kick.set()

    def start_background(self) -> None:
        if not self.market:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._kick.clear()

        def worker():
            # Startup gets a clear runway; after that explicit Advisor/Candidate
            # activity can wake the warmer immediately.
            if self._stop.wait(20.0):
                return
            while not self._stop.is_set():
                try:
                    self.run_once(max_items=2)
                except Exception as exc:
                    try:
                        self.store.set_setting("evidence_warmer:status",{
                            "ok":False,"last_run_at":time.time(),"error":str(exc)[:300],
                        })
                    except Exception:
                        pass
                self._kick.clear()
                # Either a new high-value target wakes us, or we rotate quietly.
                self._kick.wait(180.0)
                if self._stop.is_set():
                    break

        self._thread=threading.Thread(
            target=worker,name="lp-manager-evidence-warmer",daemon=True,
        )
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        self._kick.set()
