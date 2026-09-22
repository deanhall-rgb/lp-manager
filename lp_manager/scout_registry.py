from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class ChainTarget:
    name: str
    chain_id: int
    slug: str
    enabled: bool
    core_enabled: bool
    tactical_enabled: bool
    protocol_adapters: tuple[str, ...]
    cost_class: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["protocol_adapters"] = list(self.protocol_adapters)
        return row


# Discovery is intentionally chain-agnostic. Adding a chain means adding a data/execution
# adapter, not changing portfolio strategy. V0.2 begins with chains where concentrated LP
# markets are mature/useful or already supported by the existing codebase.
DEFAULT_CHAIN_UNIVERSE: tuple[ChainTarget, ...] = (
    ChainTarget("Ethereum", 1, "ethereum", True, True, True, ("UNISWAP_V3",), "HIGH", "Core-quality pools; execution cost must be modelled carefully."),
    ChainTarget("Base", 8453, "base", True, True, True, ("UNISWAP_V3",), "LOW", "Strong candidate for lower-cost core and tactical LPs."),
    ChainTarget("Arbitrum", 42161, "arbitrum", True, True, True, ("UNISWAP_V3",), "LOW"),
    ChainTarget("Optimism", 10, "optimism", True, True, True, ("UNISWAP_V3",), "LOW"),
    ChainTarget("Polygon", 137, "polygon", True, True, True, ("UNISWAP_V3",), "LOW"),
    ChainTarget("Robinhood Chain", 4663, "robinhood", True, False, True, ("UNISWAP_V3",), "LOW", "Retain as a tactical source while durability evidence is still developing."),
)


def scout_universe() -> list[dict[str, Any]]:
    return [target.to_dict() for target in DEFAULT_CHAIN_UNIVERSE if target.enabled]
