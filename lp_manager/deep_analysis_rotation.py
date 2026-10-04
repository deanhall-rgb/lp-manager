from __future__ import annotations

import threading
import time
from typing import Any

from .profit_engine import recommend_profit_range
from .provider_coordinator import ProviderDeferred
from .fx import display_amount_to_usd, money_context


class DeepAnalysisRotationService:
    """Background Profit Lab rotation for the persistent leaderboard.

    v0.9.7.5 deliberately uses one common comparison basis across the board:
    £/$1,000 equivalent capital, 7-day horizon, AUTO sleeve, 10% monthly target.
    Tactical/Core-specific board views are a later patch; this service exists to
    remove the operator's need to click 20-25 pools manually just to obtain deep
    evidence.

    Provider calls run at background priority. One candidate is analysed per
    cycle so interactive Candidate Universe / Profit Lab work always wins.
    """

    STANDARD_HORIZON_DAYS = 7.0
    STANDARD_CAPITAL_DISPLAY = 1000.0
    STANDARD_MONTHLY_TARGET_PCT = 10.0
    TARGET_LIMIT = 25
    TOP_ZONE = 20

    def __init__(self, settings, store, market, leaderboard, provider_coordinator=None):
        self.settings = settings
        self.store = store
        self.market = market
        self.leaderboard = leaderboard
        self.coordinator = provider_coordinator
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._retry_after: dict[str, float] = {}
        self._current: dict[str, Any] | None = None
        self._session_completed = 0
        self._session_failed = 0
        self._session_deferred = 0

    @staticmethod
    def _key(row: dict[str, Any]) -> str:
        return f"{str(row.get('chain') or '').upper()}:{str(row.get('pool_address') or '').lower()}"

    @staticmethod
    def _deep_status(row: dict[str, Any]) -> str:
        return str((row.get("deep_analysis") or {}).get("status") or "NOT_ANALYSED").upper()

    @classmethod
    def _needs_analysis(cls, row: dict[str, Any]) -> bool:
        if not row.get("leaderboard_eligible"):
            return False
        if str(row.get("protocol_version") or "").upper() != "V3":
            return False
        readiness = row.get("profit_lab_readiness") or {}
        if readiness.get("ready") is not True:
            return False
        return cls._deep_status(row) in {
            "NOT_ANALYSED",
            "DEEP_RECHECK_REQUIRED",
            "DEEP_STALE",
        }

    @classmethod
    def _priority(cls, row: dict[str, Any]) -> tuple[int, int, float]:
        status = cls._deep_status(row)
        status_priority = {
            "DEEP_RECHECK_REQUIRED": 0,
            "NOT_ANALYSED": 1,
            "DEEP_STALE": 2,
        }.get(status, 9)
        rank = int(row.get("rank") or 999)
        score = float((row.get("opportunity_score") or {}).get("score") or 0)
        return status_priority, rank, -score

    def targets(self) -> list[dict[str, Any]]:
        board = self.leaderboard.rebuild(limit=self.TARGET_LIMIT)
        rows = [dict(x) for x in (board.get("rows") or [])]
        now = time.time()
        out = []
        for row in rows:
            if not self._needs_analysis(row):
                continue
            key = self._key(row)
            if not key or key.startswith(":") or key.endswith(":"):
                continue
            if now < float(self._retry_after.get(key) or 0):
                continue
            row["rotation_zone"] = "TOP_20" if int(row.get("rank") or 999) <= self.TOP_ZONE else "CHALLENGER_21_25"
            out.append(row)
        out.sort(key=self._priority)
        return out

    def _persist_status(self, payload: dict[str, Any]) -> None:
        try:
            self.store.set_setting("deep_analysis_rotation:status", payload)
        except Exception:
            pass

    def run_once(self, *, max_items: int = 1) -> dict[str, Any]:
        started = time.time()
        targets = self.targets()
        selected = targets[:max(1, min(2, int(max_items)))]
        results = []
        money = money_context(self.settings, self.store)
        display_currency = str(money.get("display_currency") or "USD")
        capital_usd = max(
            1.0,
            float(display_amount_to_usd(self.STANDARD_CAPITAL_DISPLAY, self.settings, self.store)),
        )

        for row in selected:
            chain = str(row.get("chain") or "").upper()
            address = str(row.get("pool_address") or "")
            key = self._key(row)
            with self._lock:
                self._current = {
                    "chain": chain,
                    "pool_address": address,
                    "pair": row.get("pair"),
                    "rank": row.get("rank"),
                    "zone": row.get("rotation_zone"),
                    "started_at": time.time(),
                }
            try:
                if self.coordinator is not None:
                    with self.coordinator.background_work():
                        result = recommend_profit_range(
                            self.market,
                            self.store,
                            chain,
                            address,
                            horizon_days=self.STANDARD_HORIZON_DAYS,
                            capital=capital_usd,
                            sleeve="AUTO",
                            monthly_target_pct=self.STANDARD_MONTHLY_TARGET_PCT,
                            pool_fallback=row,
                            compare_fee_tiers=False,
                        )
                else:
                    result = recommend_profit_range(
                        self.market,
                        self.store,
                        chain,
                        address,
                        horizon_days=self.STANDARD_HORIZON_DAYS,
                        capital=capital_usd,
                        sleeve="AUTO",
                        monthly_target_pct=self.STANDARD_MONTHLY_TARGET_PCT,
                        pool_fallback=row,
                        compare_fee_tiers=False,
                    )
                result["generated_at"] = time.time()
                result["analysis_source"] = "BACKGROUND_LEADERBOARD_V0975"
                result["leaderboard_deep_basis"] = {
                    "capital_usd": round(capital_usd, 2),
                    "capital_display": self.STANDARD_CAPITAL_DISPLAY,
                    "display_currency": display_currency,
                    "horizon_days": self.STANDARD_HORIZON_DAYS,
                    "monthly_target_pct": self.STANDARD_MONTHLY_TARGET_PCT,
                    "sleeve": "AUTO",
                    "rotation_zone": row.get("rotation_zone"),
                    "rank_at_analysis": row.get("rank"),
                }
                audit = self.store.record_forecast_snapshot(
                    result, model_version="v0.9.7.5-background"
                )
                result["forecast_snapshot_id"] = audit.get("id")
                self._retry_after.pop(key, None)
                self._session_completed += 1
                best = result.get("recommended_range") or {}
                forecast = best.get("forecast") or {}
                results.append({
                    "ok": True,
                    "status": "ANALYSED",
                    "chain": chain,
                    "pool_address": address,
                    "pair": row.get("pair"),
                    "rank": row.get("rank"),
                    "zone": row.get("rotation_zone"),
                    "expected_net_usd": forecast.get("expected_net_usd"),
                    "modelled_position_apr_pct": forecast.get("modelled_position_apr_pct"),
                    "range_quality_score": best.get("range_quality_score"),
                    "forecast_snapshot_id": audit.get("id"),
                })
            except ProviderDeferred as exc:
                self._retry_after[key] = time.time() + 120.0
                self._session_deferred += 1
                results.append({
                    "ok": False,
                    "status": "DEFERRED_FOR_INTERACTIVE_TRAFFIC",
                    "chain": chain,
                    "pool_address": address,
                    "pair": row.get("pair"),
                    "rank": row.get("rank"),
                    "error": str(exc)[:260],
                })
            except Exception as exc:
                self._retry_after[key] = time.time() + 900.0
                self._session_failed += 1
                results.append({
                    "ok": False,
                    "status": "FAILED_RETRY_LATER",
                    "chain": chain,
                    "pool_address": address,
                    "pair": row.get("pair"),
                    "rank": row.get("rank"),
                    "error": str(exc)[:300],
                })
            finally:
                with self._lock:
                    self._current = None

        try:
            board = self.leaderboard.rebuild(limit=self.TARGET_LIMIT)
            pending = sum(
                1 for x in (board.get("rows") or [])
                if self._needs_analysis(x)
            )
            deep_positive = int((board.get("funnel") or {}).get("allocation_confirmed") or 0)
            deep_analysed = int((board.get("funnel") or {}).get("deep_analysed") or 0)
        except Exception:
            pending = len(self.targets())
            deep_positive = 0
            deep_analysed = 0

        payload = {
            "ok": all(x.get("ok") for x in results) if results else True,
            "enabled": bool(self.market),
            "mode": "TOP_20_PLUS_5_CHALLENGER_ROTATION",
            "standard_basis": {
                "capital_usd": round(capital_usd, 2),
                    "capital_display": self.STANDARD_CAPITAL_DISPLAY,
                    "display_currency": display_currency,
                "horizon_days": self.STANDARD_HORIZON_DAYS,
                "monthly_target_pct": self.STANDARD_MONTHLY_TARGET_PCT,
                "sleeve": "AUTO",
            },
            "target_limit": self.TARGET_LIMIT,
            "top_zone": self.TOP_ZONE,
            "pending": pending,
            "deep_analysed": deep_analysed,
            "deep_positive": deep_positive,
            "processed": len(results),
            "session_completed": self._session_completed,
            "session_failed": self._session_failed,
            "session_deferred": self._session_deferred,
            "last_run_at": time.time(),
            "elapsed_ms": round((time.time() - started) * 1000.0, 1),
            "results": results[-3:],
        }
        self._persist_status(payload)
        return payload

    def status(self) -> dict[str, Any]:
        try:
            payload = self.store.get_setting("deep_analysis_rotation:status", {}) or {}
        except Exception:
            payload = {}
        with self._lock:
            current = dict(self._current or {})
        pending = payload.get("pending")
        try:
            if pending is None:
                pending = len(self.targets())
        except Exception:
            pending = None
        return {
            **payload,
            "enabled": bool(self.market),
            "background_refresh": bool(self._thread and self._thread.is_alive()),
            "current": current or None,
            "pending": pending,
            "session_completed": self._session_completed,
            "session_failed": self._session_failed,
            "session_deferred": self._session_deferred,
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
            # UI/live wallet/candidate services get first use of the providers.
            if self._stop.wait(35.0):
                return
            while not self._stop.is_set():
                try:
                    self.run_once(max_items=1)
                except Exception as exc:
                    self._persist_status({
                        "ok": False,
                        "enabled": True,
                        "last_run_at": time.time(),
                        "error": str(exc)[:300],
                    })
                self._kick.clear()
                # One deep candidate per quiet cycle. Explicit candidate refreshes
                # may wake us sooner; provider background priority still protects
                # interactive traffic if the operator is actively using the UI.
                self._kick.wait(75.0)
                if self._stop.is_set():
                    break

        self._thread = threading.Thread(
            target=worker,
            name="lp-manager-deep-analysis-rotation",
            daemon=True,
        )
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        self._kick.set()
