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
    public_rpc_url: str = ""
    native_symbol: str = "ETH"
    wrapped_native: str = ""
    alchemy_slug: str = ""
    explorer_api_base: str = ""

    def configured_rpc_url(self) -> str:
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

    def rpc_url(self) -> str:
        explicit = self.configured_rpc_url()
        if explicit:
            return explicit
        alchemy_key = os.getenv("ALCHEMY_API_KEY", "").strip()
        if alchemy_key and self.alchemy_slug:
            return f"https://{self.alchemy_slug}.g.alchemy.com/v2/{alchemy_key}"
        if os.getenv("LP_MANAGER_ALLOW_PUBLIC_RPC", "true").strip().lower() in {"1", "true", "yes", "on"}:
            return self.public_rpc_url
        return ""

    def rpc_source(self) -> str:
        if self.configured_rpc_url():
            return "CONFIGURED"
        if os.getenv("ALCHEMY_API_KEY", "").strip() and self.alchemy_slug:
            return "ALCHEMY"
        if self.rpc_url():
            return "PUBLIC_FALLBACK"
        return "MISSING"

    def enabled(self) -> bool:
        raw = os.getenv(f"LP_MANAGER_{self.key}_ENABLED")
        if raw is None:
            return self.enabled_default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def scan_blocks(self, global_default: int) -> int:
        raw = os.getenv(f"LP_MANAGER_{self.key}_SCAN_BLOCKS", "").strip()
        return max(1000, int(raw)) if raw else max(1000, global_default or self.scan_blocks_default)


CHAINS: dict[str, ChainConfig] = {
    "ETHEREUM": ChainConfig(
        "ETHEREUM", "Ethereum", 1, "ETHEREUM_RPC_URL", "eth",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        public_rpc_url="https://cloudflare-eth.com",
        wrapped_native="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        alchemy_slug="eth-mainnet",
    ),
    "BASE": ChainConfig(
        "BASE", "Base", 8453, "BASE_RPC_URL", "base",
        "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
        "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        public_rpc_url="https://mainnet.base.org",
        wrapped_native="0x4200000000000000000000000000000000000006",
        alchemy_slug="base-mainnet",
    ),
    "ARBITRUM": ChainConfig(
        "ARBITRUM", "Arbitrum", 42161, "ARBITRUM_RPC_URL", "arbitrum",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        public_rpc_url="https://arb1.arbitrum.io/rpc",
        wrapped_native="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        alchemy_slug="arb-mainnet",
    ),
    "OPTIMISM": ChainConfig(
        "OPTIMISM", "Optimism", 10, "OPTIMISM_RPC_URL", "optimism",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        public_rpc_url="https://mainnet.optimism.io",
        wrapped_native="0x4200000000000000000000000000000000000006",
        alchemy_slug="opt-mainnet",
    ),
    "POLYGON": ChainConfig(
        "POLYGON", "Polygon", 137, "POLYGON_RPC_URL", "polygon_pos",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        public_rpc_url="https://polygon-rpc.com",
        native_symbol="POL",
        wrapped_native="0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        alchemy_slug="polygon-mainnet",
    ),
    "ROBINHOOD_CHAIN": ChainConfig(
        "ROBINHOOD_CHAIN", "Robinhood Chain", 4663, "RH_RPC_URL", "robinhood",
        "0x73991a25C818Bf1f1128dEAaB1492D45638DE0D3",
        "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA",
        scan_blocks_default=150_000,
        public_rpc_url="https://rpc.mainnet.chain.robinhood.com",
        wrapped_native="0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        alchemy_slug="robinhood-mainnet",
        explorer_api_base="https://robinhoodchain.blockscout.com/api/v2",
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
        row["rpc_source"] = cfg.rpc_source()
        # Never expose a configured endpoint because URLs frequently contain API keys.
        row["rpc_env_present"] = bool(cfg.configured_rpc_url())
        row.pop("public_rpc_url", None)
        row.pop("enabled_default", None)
        out.append(row)
    return out
