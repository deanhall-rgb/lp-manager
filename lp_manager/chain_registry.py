from __future__ import annotations

import os
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ChainConfig:
    key: str
    name: str
    chain_id: int
    rpc_env: str
    gecko_network: str
    position_manager: str
    factory: str
    enabled_default: bool = True
    scan_blocks_default: int = 500_000

    def rpc_url(self) -> str:
        aliases = {
            "ETHEREUM_RPC_URL": ("ETHEREUM_RPC_URL", "ETH_RPC_URL", "MAINNET_RPC_URL"),
            "BASE_RPC_URL": ("BASE_RPC_URL",),
            "ARBITRUM_RPC_URL": ("ARBITRUM_RPC_URL", "ARB_RPC_URL"),
            "OPTIMISM_RPC_URL": ("OPTIMISM_RPC_URL", "OP_RPC_URL"),
            "POLYGON_RPC_URL": ("POLYGON_RPC_URL", "MATIC_RPC_URL"),
            "RH_RPC_URL": ("RH_RPC_URL", "ROBINHOOD_RPC_URL"),
        }
        for name in aliases.get(self.rpc_env, (self.rpc_env,)):
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    def enabled(self) -> bool:
        raw = os.getenv(f"LP_MANAGER_{self.key}_ENABLED")
        if raw is None:
            return self.enabled_default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def scan_blocks(self, global_default: int) -> int:
        raw = os.getenv(f"LP_MANAGER_{self.key}_SCAN_BLOCKS", "").strip()
        return max(1000, int(raw)) if raw else max(1000, global_default or self.scan_blocks_default)


# Official canonical V3 deployments are used for Ethereum/Arbitrum/Optimism/Polygon.
# Base uses its Base deployment. Robinhood is the deployment already proven by
# the legacy bot and remains isolated as a chain-specific adapter.
CHAINS: dict[str, ChainConfig] = {
    "ETHEREUM": ChainConfig(
        "ETHEREUM", "Ethereum", 1, "ETHEREUM_RPC_URL", "eth",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    ),
    "BASE": ChainConfig(
        "BASE", "Base", 8453, "BASE_RPC_URL", "base",
        "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
        "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    ),
    "ARBITRUM": ChainConfig(
        "ARBITRUM", "Arbitrum", 42161, "ARBITRUM_RPC_URL", "arbitrum",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    ),
    "OPTIMISM": ChainConfig(
        "OPTIMISM", "Optimism", 10, "OPTIMISM_RPC_URL", "optimism",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    ),
    "POLYGON": ChainConfig(
        "POLYGON", "Polygon", 137, "POLYGON_RPC_URL", "polygon_pos",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    ),
    "ROBINHOOD_CHAIN": ChainConfig(
        "ROBINHOOD_CHAIN", "Robinhood Chain", 4663, "RH_RPC_URL", "robinhood",
        "0x73991a25C818Bf1f1128dEAaB1492D45638DE0D3",
        "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA",
        scan_blocks_default=150_000,
    ),
}


def chain_config(key: str) -> ChainConfig:
    normal = str(key or "").upper()
    if normal not in CHAINS:
        raise KeyError(f"Unsupported chain: {key}")
    return CHAINS[normal]


def registry_status() -> list[dict]:
    out = []
    for cfg in CHAINS.values():
        row = asdict(cfg)
        row["enabled"] = cfg.enabled()
        row["rpc_configured"] = bool(cfg.rpc_url())
        row.pop("enabled_default", None)
        out.append(row)
    return out
