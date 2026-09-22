from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class ScheduledJob:
    key: str
    owner: str
    interval_seconds: float
    event_driven: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


JOBS = (
    ScheduledJob("market_refresh", "lp_manager.scheduler", 60.0),
    ScheduledJob("owned_position_refresh", "lp_manager.scheduler", 60.0),
    ScheduledJob("scout_discovery", "lp_manager.scheduler", 15 * 60.0),
    ScheduledJob("opportunity_recheck", "lp_manager.scheduler", 30 * 60.0),
    ScheduledJob("portfolio_accounting", "lp_manager.scheduler", 5 * 60.0),
    ScheduledJob("strategy_review", "lp_manager.scheduler", 0.0, event_driven=True),
    ScheduledJob("ai_review", "lp_manager.scheduler", 0.0, event_driven=True),
    ScheduledJob("notifications", "lp_manager.scheduler", 0.0, event_driven=True),
)


def scheduler_manifest() -> list[dict[str, Any]]:
    return [job.to_dict() for job in JOBS]


def due_jobs(last_run: dict[str, float] | None = None, *, now: float | None = None) -> list[dict[str, Any]]:
    """Return routine jobs due now. Event-driven lanes are deliberately excluded."""
    last_run = last_run or {}
    now = float(now or time.time())
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in JOBS:
        if job.key in seen:
            raise RuntimeError(f"DUPLICATE_SCHEDULER_JOB:{job.key}")
        seen.add(job.key)
        if job.event_driven:
            continue
        last = float(last_run.get(job.key) or 0.0)
        due = last <= 0 or now - last >= job.interval_seconds
        if due:
            out.append({**job.to_dict(), "last_run": last or None, "overdue_seconds": max(0.0, now - last - job.interval_seconds) if last else None})
    return out


def assert_single_owner(manifest: list[dict[str, Any]] | None = None) -> None:
    rows = manifest or scheduler_manifest()
    keys: set[str] = set()
    for row in rows:
        key = str(row.get("key") or "")
        owner = str(row.get("owner") or "")
        if not key or not owner:
            raise RuntimeError("SCHEDULER_JOB_MISSING_KEY_OR_OWNER")
        if key in keys:
            raise RuntimeError(f"DUPLICATE_SCHEDULER_JOB:{key}")
        keys.add(key)
