from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Any


ALLOWED_ACTIONS = {"OPEN", "COLLECT", "CLOSE", "INCREASE", "DECREASE", "RECENTER", "COMPOUND"}


@dataclass
class TransactionPlan:
    action: str
    chain: str
    protocol: str
    wallet: str | None = None
    position_id: str | None = None
    pair: str | None = None
    intent: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    simulation: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    status: str = "DRAFT"
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_transaction_plan(plan: TransactionPlan | dict[str, Any]) -> dict[str, Any]:
    row = plan.to_dict() if isinstance(plan, TransactionPlan) else dict(plan)
    errors: list[str] = []
    warnings: list[str] = []
    action = str(row.get("action") or "").upper()
    if action not in ALLOWED_ACTIONS:
        errors.append("INVALID_ACTION")
    for field_name in ("chain", "protocol"):
        if not str(row.get(field_name) or "").strip():
            errors.append(f"MISSING_{field_name.upper()}")
    if action in {"COLLECT", "CLOSE", "INCREASE", "DECREASE", "RECENTER", "COMPOUND"} and not row.get("position_id"):
        errors.append("POSITION_ID_REQUIRED")
    if action in {"OPEN", "RECENTER"}:
        intent = row.get("intent") or {}
        lower = float(intent.get("lower_price") or 0.0)
        upper = float(intent.get("upper_price") or 0.0)
        capital = float(intent.get("capital_value") or 0.0)
        if lower <= 0 or upper <= lower:
            errors.append("INVALID_RANGE")
        if action == "OPEN" and capital <= 0:
            errors.append("INVALID_CAPITAL")
    if not row.get("calls"):
        warnings.append("NO_CALLDATA_BUILT")
    if not row.get("simulation"):
        warnings.append("NOT_SIMULATED")
    return {"valid": not errors, "errors": sorted(set(errors)), "warnings": sorted(set(warnings)), "action": action}
