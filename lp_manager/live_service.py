from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .chain_registry import CHAINS, registry_status
from .market_data import GeckoTerminalClient

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _valid_address(value: str) -> bool:
    return bool(_ADDR_RE.fullmatch(str(value or "").strip()))


class LiveDataService:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.market = GeckoTerminalClient() if settings.gecko_terminal_enabled else None
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        wallet = self.settings.wallet_address
        last = self.store.get_setting("live:last_refresh", None)
        return {
            "live_mode_ready": bool(wallet and _valid_address(wallet)),
            "wallet_configured": bool(wallet),
            "wallet_valid": bool(wallet and _valid_address(wallet)),
            "wallet_display": f"{wallet[:8]}…{wallet[-6:]}" if len(wallet) > 16 else wallet,
            "demo_seed": self.settings.demo_seed,
            "market_data": {"geckoterminal": bool(self.market), "thegraph": bool(self.settings.thegraph_api_key)},
            "chains": registry_status(),
            "last_refresh": last,
            "refresh_seconds": self.settings.live_refresh_seconds,
            "background_refresh": bool(self._thread and self._thread.is_alive()),
            "signing_enabled": False,
            "broadcast_enabled": False,
        }

    def refresh_positions(self, chains: list[str] | None = None) -> dict[str, Any]:
        from .live_positions import reconcile_scan, scan_chain_positions

        if not self._refresh_lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "A live refresh is already running", "chains": []}
        try:
            selected = [c for c in (chains or list(CHAINS)) if c in CHAINS and CHAINS[c].enabled()]
            if not self.settings.wallet_address or not _valid_address(self.settings.wallet_address):
                return {"ok": False, "error": "Configure a valid WALLET_ADDRESS in .env", "chains": []}
            configured = [c for c in selected if CHAINS[c].rpc_url()]
            results = []
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(configured)))) as pool:
                futures = {}
                for c in configured:
                    checkpoint = self.store.get_setting(f"live:checkpoint:{c}", None)
                    from_override = max(0, int(checkpoint) - 128) if checkpoint is not None else None
                    known = self.store.live_token_ids(c)
                    f = pool.submit(
                        scan_chain_positions, CHAINS[c], self.settings.wallet_address,
                        scan_blocks=CHAINS[c].scan_blocks(self.settings.position_scan_blocks), market=self.market,
                        from_block_override=from_override, known_token_ids=known,
                    )
                    futures[f] = c
                for f in as_completed(futures):
                    c = futures[f]
                    try:
                        scan = f.result(); row = reconcile_scan(self.store, scan); results.append(row)
                        if scan.ok and scan.latest_block is not None:
                            self.store.set_setting(f"live:checkpoint:{c}", int(scan.latest_block))
                    except Exception as exc:
                        results.append({"chain": c, "ok": False, "error": str(exc)})
            for c in selected:
                if c not in configured:
                    results.append({"chain": c, "ok": False, "skipped": True, "error": f"{CHAINS[c].rpc_env} not configured"})
            payload = {"ok": any(r.get("ok") for r in results), "read_at": time.time(), "chains": sorted(results, key=lambda r: r.get("chain", ""))}
            self.store.set_setting("live:last_refresh", payload)
            return payload
        finally:
            self._refresh_lock.release()

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.settings.wallet_address or not _valid_address(self.settings.wallet_address):
            return
        if not any(cfg.enabled() and cfg.rpc_url() for cfg in CHAINS.values()):
            return
        self._stop.clear()

        def worker():
            # Small delay lets the web server become responsive before the first chain scan.
            if self._stop.wait(2.0): return
            while not self._stop.is_set():
                try: self.refresh_positions()
                except Exception: pass
                if self._stop.wait(self.settings.live_refresh_seconds): break

        self._thread = threading.Thread(target=worker, name="lp-manager-live-refresh", daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
