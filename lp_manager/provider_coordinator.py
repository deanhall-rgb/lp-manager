from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable


class ProviderCoordinatorError(RuntimeError):
    pass


class ProviderDeferred(ProviderCoordinatorError):
    """Background work yielded to recent/active interactive provider traffic."""


@dataclass(frozen=True)
class ProviderPolicy:
    min_gap_seconds: float = 0.0
    max_concurrent: int = 2
    cooldown_seconds: float = 20.0
    interactive_quiet_seconds: float = 8.0


DEFAULT_POLICIES: dict[str, ProviderPolicy] = {
    "GECKOTERMINAL": ProviderPolicy(0.62, 1, 30.0, 10.0),
    "ALCHEMY": ProviderPolicy(0.04, 2, 20.0, 6.0),
    "DEXSCREENER": ProviderPolicy(0.10, 2, 20.0, 5.0),
    "THEGRAPH": ProviderPolicy(0.05, 2, 20.0, 5.0),
    "RPC": ProviderPolicy(0.0, 4, 12.0, 3.0),
}


class ProviderCoordinator:
    """Shared request budget, coalescing and short-lived cache for provider traffic.

    This object is intentionally process-local. Durable market evidence remains in
    the existing Store (Candidate Universe snapshots, Profit Lab history, wallet and
    position snapshots). The coordinator prevents concurrent product surfaces from
    independently spending the same provider request while those durable caches are
    being filled.
    """

    def __init__(self, store=None, policies: dict[str, ProviderPolicy] | None = None):
        self.store = store
        self.policies = {**DEFAULT_POLICIES, **(policies or {})}
        self._lock = threading.RLock()
        self._provider_locks = {
            name: threading.Semaphore(max(1, int(policy.max_concurrent)))
            for name, policy in self.policies.items()
        }
        self._cache: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self._last_started: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}
        self._last_interactive: dict[str, float] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._local = threading.local()
        self._last_persisted_at = 0.0

    def _policy(self, provider: str) -> ProviderPolicy:
        key = str(provider or "UNKNOWN").upper()
        return self.policies.get(key, ProviderPolicy())

    def _stat(self, provider: str) -> dict[str, Any]:
        key = str(provider or "UNKNOWN").upper()
        with self._lock:
            return self._stats.setdefault(key, {
                "requests": 0,
                "cache_hits": 0,
                "stale_hits": 0,
                "coalesced_waits": 0,
                "errors": 0,
                "rate_limits": 0,
                "background_deferred": 0,
                "latency_ms_total": 0.0,
                "last_request_at": 0.0,
                "last_error": None,
            })

    def _cache_key(self, provider: str, key: str) -> str:
        return f"{str(provider or 'UNKNOWN').upper()}::{str(key)}"

    def _cache_row(self, full_key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._cache.get(full_key)

    @staticmethod
    def _rate_limited(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "429" in msg or "rate limit" in msg or "too many requests" in msg

    @contextmanager
    def background_work(self):
        previous = getattr(self._local, "priority", "interactive")
        self._local.priority = "background"
        try:
            yield
        finally:
            self._local.priority = previous

    def priority(self) -> str:
        return str(getattr(self._local, "priority", "interactive") or "interactive")

    def request(
        self,
        provider: str,
        key: str,
        loader: Callable[[], Any],
        *,
        ttl_seconds: float = 0.0,
        stale_seconds: float = 0.0,
        wait_timeout_seconds: float = 35.0,
    ) -> Any:
        provider = str(provider or "UNKNOWN").upper()
        policy = self._policy(provider)
        full_key = self._cache_key(provider, key)
        now = time.time()
        priority = self.priority()
        stat = self._stat(provider)

        with self._lock:
            row = self._cache.get(full_key)
            age = now - float(row.get("saved_at") or 0) if row else float("inf")
            if row is not None and ttl_seconds > 0 and age <= ttl_seconds:
                stat["cache_hits"] += 1
                self._persist_status_maybe()
                return row.get("value")

            cooldown = float(self._cooldown_until.get(provider) or 0)
            if now < cooldown:
                if row is not None and stale_seconds > 0 and age <= stale_seconds:
                    stat["stale_hits"] += 1
                    self._persist_status_maybe()
                    return row.get("value")
                raise ProviderCoordinatorError(
                    f"{provider} provider cooldown active for {round(cooldown-now,1)}s"
                )

            if priority == "background":
                recent = now - float(self._last_interactive.get(provider) or 0)
                provider_busy = any(
                    k.startswith(provider + "::") for k in self._inflight
                )
                if provider_busy or recent < policy.interactive_quiet_seconds:
                    stat["background_deferred"] += 1
                    if row is not None and stale_seconds > 0 and age <= stale_seconds:
                        stat["stale_hits"] += 1
                        self._persist_status_maybe()
                        return row.get("value")
                    self._persist_status_maybe()
                    raise ProviderDeferred(
                        f"{provider} background request deferred for interactive traffic"
                    )

            existing = self._inflight.get(full_key)
            if existing is not None:
                stat["coalesced_waits"] += 1
                event = existing
                owner = False
            else:
                event = threading.Event()
                self._inflight[full_key] = event
                owner = True
                if priority != "background":
                    self._last_interactive[provider] = now

        if not owner:
            event.wait(max(0.1, float(wait_timeout_seconds)))
            with self._lock:
                row = self._cache.get(full_key)
                if row is not None:
                    self._persist_status_maybe()
                    return row.get("value")
            raise ProviderCoordinatorError(
                f"{provider} coalesced request completed without reusable data"
            )

        semaphore = self._provider_locks.setdefault(
            provider, threading.Semaphore(max(1, policy.max_concurrent))
        )
        started = time.perf_counter()
        try:
            with semaphore:
                with self._lock:
                    gap = policy.min_gap_seconds - (
                        time.monotonic() - float(self._last_started.get(provider) or 0)
                    )
                if gap > 0:
                    time.sleep(gap)
                with self._lock:
                    self._last_started[provider] = time.monotonic()
                value = loader()
                finished = time.time()
                with self._lock:
                    self._cache[full_key] = {
                        "saved_at": finished,
                        "value": value,
                    }
                    stat["requests"] += 1
                    stat["last_request_at"] = finished
                    stat["latency_ms_total"] += (time.perf_counter() - started) * 1000.0
                    stat["last_error"] = None
                self._persist_status_maybe()
                return value
        except Exception as exc:
            limited = self._rate_limited(exc)
            with self._lock:
                stat["requests"] += 1
                stat["errors"] += 1
                stat["last_request_at"] = time.time()
                stat["latency_ms_total"] += (time.perf_counter() - started) * 1000.0
                stat["last_error"] = str(exc)[:220]
                if limited:
                    stat["rate_limits"] += 1
                    self._cooldown_until[provider] = max(
                        float(self._cooldown_until.get(provider) or 0),
                        time.time() + policy.cooldown_seconds,
                    )
                row = self._cache.get(full_key)
                age = time.time() - float(row.get("saved_at") or 0) if row else float("inf")
                if row is not None and stale_seconds > 0 and age <= stale_seconds:
                    stat["stale_hits"] += 1
                    self._persist_status_maybe()
                    return row.get("value")
            self._persist_status_maybe()
            raise
        finally:
            with self._lock:
                done = self._inflight.pop(full_key, None)
                if done is not None:
                    done.set()

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            providers = {}
            for provider in sorted(set(self.policies) | set(self._stats)):
                stat = dict(self._stats.get(provider) or {})
                requests = int(stat.get("requests") or 0)
                latency = float(stat.get("latency_ms_total") or 0)
                providers[provider] = {
                    **stat,
                    "avg_latency_ms": round(latency / requests, 1) if requests else 0.0,
                    "cooldown_seconds_remaining": round(
                        max(0.0, float(self._cooldown_until.get(provider) or 0) - now), 1
                    ),
                }
            return {
                "enabled": True,
                "cache_entries": len(self._cache),
                "inflight": len(self._inflight),
                "priority_model": "INTERACTIVE_FIRST",
                "providers": providers,
                "totals": {
                    "requests": sum(int(x.get("requests") or 0) for x in providers.values()),
                    "cache_hits": sum(int(x.get("cache_hits") or 0) for x in providers.values()),
                    "stale_hits": sum(int(x.get("stale_hits") or 0) for x in providers.values()),
                    "coalesced_waits": sum(int(x.get("coalesced_waits") or 0) for x in providers.values()),
                    "rate_limits": sum(int(x.get("rate_limits") or 0) for x in providers.values()),
                    "background_deferred": sum(int(x.get("background_deferred") or 0) for x in providers.values()),
                },
            }

    def _persist_status_maybe(self) -> None:
        if self.store is None:
            return
        now = time.time()
        if now - self._last_persisted_at < 5.0:
            return
        self._last_persisted_at = now
        try:
            self.store.set_setting("provider_coordinator:status", self.status())
        except Exception:
            pass
