from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Position:
    id: str
    protocol: str
    chain: str
    pair: str
    status: str
    lower_price: float
    upper_price: float
    current_price: float
    capital_value: float
    current_value: float
    unclaimed_fees: float
    fees_today: float
    fees_7d: float
    fees_30d: float
    realised_fees: float
    estimated_il: float
    gas_costs: float
    apr_current: float
    apr_7d: float
    opened_at: float
    token_id: str | None = None
    campaign_id: str | None = None
    source: str = "manual"
    notes: str = ""
    strategy_sleeve: str = "TACTICAL_CAMPAIGN"
    directional_bias: str = "NEUTRAL"
    inventory_intent: str = "BALANCED"
    target_hold_days: float = 3.0
    monitoring_class: str = "ACTIVE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    id: str
    position_id: str | None
    created_at: float
    severity: str
    action: str
    confidence: float
    summary: str
    rationale: str
    trigger: str
    status: str = "OPEN"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
