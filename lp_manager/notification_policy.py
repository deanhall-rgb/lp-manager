from __future__ import annotations

import hashlib
import time
from typing import Any


SEVERITY_ORDER = {"INFO": 0, "WATCH": 1, "ACTION": 2, "EXECUTION": 3, "CRITICAL": 4}
DEFAULT_COOLDOWNS = {"INFO": 6 * 3600, "WATCH": 2 * 3600, "ACTION": 30 * 60, "EXECUTION": 0, "CRITICAL": 0}


def notification_key(event: dict[str, Any]) -> str:
    raw = "|".join(str(event.get(k) or "") for k in ("position_id", "pair", "severity", "code", "action"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def evaluate_notification(
    event: dict[str, Any],
    *,
    last_sent_at: float | None = None,
    now: float | None = None,
    minimum_severity: str = "WATCH",
    cooldowns: dict[str, float] | None = None,
) -> dict[str, Any]:
    now = float(now or time.time())
    severity = str(event.get("severity") or "INFO").upper()
    if severity not in SEVERITY_ORDER:
        severity = "INFO"
    minimum = str(minimum_severity or "WATCH").upper()
    cooldown = float((cooldowns or DEFAULT_COOLDOWNS).get(severity, 3600))
    reasons: list[str] = []
    send = True
    if SEVERITY_ORDER[severity] < SEVERITY_ORDER.get(minimum, 1):
        send = False
        reasons.append("BELOW_NOTIFICATION_THRESHOLD")
    if last_sent_at and cooldown > 0 and now - float(last_sent_at) < cooldown:
        send = False
        reasons.append("DEDUP_COOLDOWN_ACTIVE")
    if event.get("material") is False and severity in {"INFO", "WATCH"}:
        send = False
        reasons.append("NON_MATERIAL_EVENT")
    return {
        "send": send,
        "severity": severity,
        "key": notification_key({**event, "severity": severity}),
        "cooldown_seconds": cooldown,
        "reasons": reasons,
        "message": str(event.get("message") or event.get("summary") or event.get("code") or "LP Manager event"),
    }
