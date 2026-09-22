from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    execution_mode: str
    legacy_root: Path
    currency: str
    demo_seed: bool
    wallet_address: str = ""
    gecko_terminal_enabled: bool = True
    thegraph_api_key: str = ""
    live_refresh_seconds: int = 60
    position_scan_blocks: int = 500_000


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_env_files(root: Path) -> Path:
    # Priority: existing process environment > LP Manager .env > legacy bot .env.
    # Blank values in the LP Manager .env do not erase a working legacy value.
    process_keys = set(os.environ)
    root_values = dotenv_values(root / ".env") if (root / ".env").exists() else {}
    legacy_hint = os.getenv("LP_MANAGER_LEGACY_ROOT") or root_values.get("LP_MANAGER_LEGACY_ROOT") or str(root.parent)
    legacy_root = Path(str(legacy_hint)).resolve()
    load_dotenv(legacy_root / ".env", override=False)
    for key, value in root_values.items():
        if key in process_keys or value is None or str(value).strip() == "":
            continue
        os.environ[str(key)] = str(value)
    return Path(os.getenv("LP_MANAGER_LEGACY_ROOT", str(legacy_root))).resolve()

def load_settings(project_root: Path | None = None) -> Settings:
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    legacy_root = _load_env_files(root)

    data_dir = Path(os.getenv("LP_MANAGER_DATA_DIR", str(root / "data"))).resolve()
    return Settings(
        project_root=root,
        data_dir=data_dir,
        database_path=data_dir / "lp_manager.sqlite3",
        execution_mode=os.getenv("LP_MANAGER_EXECUTION_MODE", "build_only").strip().lower(),
        legacy_root=legacy_root,
        currency=os.getenv("LP_MANAGER_CURRENCY", "GBP").strip().upper(),
        demo_seed=_bool("LP_MANAGER_DEMO_SEED", True),
        wallet_address=os.getenv("WALLET_ADDRESS", "").strip(),
        gecko_terminal_enabled=_bool("GECKOTERMINAL_ENABLED", True),
        thegraph_api_key=os.getenv("THEGRAPH_API_KEY", "").strip(),
        live_refresh_seconds=max(15, int(os.getenv("LP_MANAGER_LIVE_REFRESH_SECONDS", "60"))),
        position_scan_blocks=max(1000, int(os.getenv("LP_MANAGER_POSITION_SCAN_BLOCKS", "500000"))),
    )
