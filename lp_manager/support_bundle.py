from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"private_key", "seed", "mnemonic", "api_key", "token", "secret", "password", "rpc_url"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(s in str(k).lower() for s in SENSITIVE_KEYS):
                out[k] = "<redacted>"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def build_support_bundle(store, *, output_dir: Path, extra: dict[str, Any] | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    path = output_dir / f"lp-manager-support-{stamp}.zip"
    payloads = {
        "positions.json": store.list_positions(),
        "opportunities.json": store.list_opportunities(500),
        "decisions.json": store.list_decisions(500),
        "actions.json": store.list_actions(500),
        "extra.json": extra or {},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in payloads.items():
            zf.writestr(name, json.dumps(_sanitize(payload), indent=2, sort_keys=True, default=str))
    return path
