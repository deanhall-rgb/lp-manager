from __future__ import annotations

import threading
import time
from typing import Any

from .chain_registry import CHAINS
from .provider_coordinator import ProviderDeferred


class CandidateUniverseMaintenanceService:
    """Quietly keeps all supported Candidate Universe snapshots usable.

    The leaderboard is a persistent product surface, so it must not depend on the
    operator manually rebuilding six chains every session. This worker refreshes
    one oldest/due chain at a time at background provider priority and preserves
    the last valid snapshot if a degraded refresh returns no usable candidates.
    """

    TARGET_REFRESH_SECONDS = 12 * 60.0
    MISSING_RETRY_SECONDS = 90.0
    FAILURE_RETRY_SECONDS = 5 * 60.0
    CYCLE_SECONDS = 75.0

    def __init__(self, store, candidate_universe, provider_coordinator=None):
        self.store = store
        self.universe = candidate_universe
        self.coordinator = provider_coordinator
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._retry_after: dict[str, float] = {}
        self._current: dict[str, Any] | None = None
        self._session_refreshed = 0
        self._session_failed = 0
        self._session_deferred = 0

    def _snapshot_state(self, chain: str) -> dict[str, Any]:
        try:
            snap = self.universe.cached(chain)
        except Exception:
            snap = {}
        generated = float(snap.get("generated_at") or 0)
        age = max(0.0, time.time() - generated) if generated > 0 else float("inf")
        return {
            "chain": chain,
            "ok": bool(snap.get("ok")),
            "generated_at": generated or None,
            "age_seconds": age,
            "shortlisted": len(snap.get("shortlist") or []),
        }

    def states(self) -> list[dict[str, Any]]:
        return [self._snapshot_state(str(chain).upper()) for chain in CHAINS]

    def _next_chain(self) -> dict[str, Any] | None:
        now = time.time()
        states = self.states()
        due = []
        for state in states:
            chain = state["chain"]
            if now < float(self._retry_after.get(chain) or 0):
                continue
            age = float(state.get("age_seconds") or float("inf"))
            if not state.get("ok") or age >= self.TARGET_REFRESH_SECONDS:
                due.append(state)
        if not due:
            return None
        due.sort(
            key=lambda x: (
                bool(x.get("ok")),
                -float(x.get("age_seconds") or 0),
            )
        )
        return due[0]

    def _persist_status(self, payload: dict[str, Any]) -> None:
        try:
            self.store.set_setting("candidate_universe_maintenance:status", payload)
        except Exception:
            pass

    def run_once(self) -> dict[str, Any]:
        started = time.time()
        target = self._next_chain()
        result: dict[str, Any] | None = None

        if target is not None:
            chain = str(target["chain"]).upper()
            with self._lock:
                self._current = {
                    "chain": chain,
                    "started_at": time.time(),
                    "prior_age_seconds": round(float(target.get("age_seconds") or 0), 1)
                    if target.get("age_seconds") != float("inf") else None,
                }
            try:
                if self.coordinator is not None:
                    with self.coordinator.background_work():
                        snap = self.universe.refresh(
                            chain,
                            graph_limit=500,
                            shortlist_limit=40,
                            validate_limit=20,
                            include_gecko=True,
                            preserve_existing_on_failure=True,
                        )
                else:
                    snap = self.universe.refresh(
                        chain,
                        graph_limit=500,
                        shortlist_limit=40,
                        validate_limit=20,
                        include_gecko=True,
                        preserve_existing_on_failure=True,
                    )
                preserved = bool(snap.get("background_refresh_preserved"))
                if preserved:
                    self._retry_after[chain] = time.time() + self.FAILURE_RETRY_SECONDS
                    self._session_failed += 1
                    status = "PRESERVED_PREVIOUS"
                else:
                    self._retry_after.pop(chain, None)
                    self._session_refreshed += 1
                    status = "REFRESHED"
                result = {
                    "ok": bool(snap.get("ok")),
                    "status": status,
                    "chain": chain,
                    "shortlisted": len(snap.get("shortlist") or []),
                    "research_ready": int((snap.get("summary") or {}).get("research_ready") or 0),
                    "generated_at": snap.get("generated_at"),
                    "providers": [
                        {
                            "provider": x.get("provider"),
                            "status": x.get("status"),
                            "error": x.get("error"),
                        }
                        for x in (snap.get("providers") or [])
                    ],
                }
            except ProviderDeferred as exc:
                self._retry_after[chain] = time.time() + self.MISSING_RETRY_SECONDS
                self._session_deferred += 1
                result = {
                    "ok": False,
                    "status": "DEFERRED_FOR_INTERACTIVE_TRAFFIC",
                    "chain": chain,
                    "error": str(exc)[:260],
                }
            except Exception as exc:
                self._retry_after[chain] = time.time() + self.FAILURE_RETRY_SECONDS
                self._session_failed += 1
                result = {
                    "ok": False,
                    "status": "FAILED_RETRY_LATER",
                    "chain": chain,
                    "error": str(exc)[:300],
                }
            finally:
                with self._lock:
                    self._current = None

        states = self.states()
        ages = [
            float(x.get("age_seconds") or 0)
            for x in states
            if x.get("ok") and x.get("age_seconds") != float("inf")
        ]
        payload = {
            "ok": result.get("ok") if result else True,
            "enabled": True,
            "target_refresh_seconds": self.TARGET_REFRESH_SECONDS,
            "chains_total": len(states),
            "chains_with_snapshots": sum(1 for x in states if x.get("ok")),
            "due_chains": sum(
                1
                for x in states
                if (not x.get("ok")) or float(x.get("age_seconds") or 0) >= self.TARGET_REFRESH_SECONDS
            ),
            "oldest_age_seconds": round(max(ages), 1) if ages else None,
            "last_run_at": time.time(),
            "elapsed_ms": round((time.time() - started) * 1000.0, 1),
            "session_refreshed": self._session_refreshed,
            "session_failed": self._session_failed,
            "session_deferred": self._session_deferred,
            "last_result": result,
            "chains": states,
        }
        self._persist_status(payload)
        return payload

    def status(self) -> dict[str, Any]:
        try:
            payload = self.store.get_setting("candidate_universe_maintenance:status", {}) or {}
        except Exception:
            payload = {}
        with self._lock:
            current = dict(self._current or {})
        return {
            **payload,
            "enabled": True,
            "background_refresh": bool(self._thread and self._thread.is_alive()),
            "current": current or None,
            "session_refreshed": self._session_refreshed,
            "session_failed": self._session_failed,
            "session_deferred": self._session_deferred,
        }

    def kick(self) -> None:
        self._kick.set()

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._kick.clear()

        def worker():
            # Run before deep analysis begins so the persistent board gets a fresh
            # source universe first. Only one chain is touched per cycle.
            if self._stop.wait(18.0):
                return
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception as exc:
                    self._persist_status({
                        "ok": False,
                        "enabled": True,
                        "last_run_at": time.time(),
                        "error": str(exc)[:300],
                    })
                self._kick.clear()
                self._kick.wait(self.CYCLE_SECONDS)
                if self._stop.is_set():
                    break

        self._thread = threading.Thread(
            target=worker,
            name="lp-manager-candidate-universe-maintenance",
            daemon=True,
        )
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        self._kick.set()
