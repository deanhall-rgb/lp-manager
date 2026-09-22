from __future__ import annotations

import time
from typing import Any

import requests

from .chain_registry import chain_config


class MarketDataError(RuntimeError):
    pass


class GeckoTerminalClient:
    BASE = "https://api.geckoterminal.com/api/v2"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "LP-Manager/0.5"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{self.BASE}{path}"
        last = None
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params or {}, timeout=20)
                if r.status_code == 429:
                    time.sleep(0.8 * (attempt + 1)); continue
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
                if attempt < 2: time.sleep(0.4 * (2 ** attempt))
        raise MarketDataError(f"GeckoTerminal request failed: {last}")

    @staticmethod
    def _included_tokens(payload: dict) -> dict[str, dict]:
        result = {}
        for row in payload.get("included") or []:
            if row.get("type") == "token":
                result[str(row.get("id"))] = row.get("attributes") or {}
        return result

    @classmethod
    def _normalise_pool(cls, row: dict, payload: dict, chain_key: str) -> dict:
        attrs = row.get("attributes") or {}
        rel = row.get("relationships") or {}
        included = cls._included_tokens(payload)
        base_id = (((rel.get("base_token") or {}).get("data") or {}).get("id"))
        quote_id = (((rel.get("quote_token") or {}).get("data") or {}).get("id"))
        dex_id = (((rel.get("dex") or {}).get("data") or {}).get("id")) or attrs.get("dex_id")
        base = included.get(str(base_id), {})
        quote = included.get(str(quote_id), {})
        vols = attrs.get("volume_usd") or {}
        txs = attrs.get("transactions") or {}
        return {
            "id": row.get("id"),
            "chain": chain_key,
            "protocol": "UNISWAP_V3" if "uniswap" in str(dex_id or "").lower() and "v3" in str(dex_id or "").lower() else str(dex_id or "DEX").upper(),
            "dex_id": dex_id,
            "pool_address": attrs.get("address"),
            "name": attrs.get("name") or f"{base.get('symbol','?')}/{quote.get('symbol','?')}",
            "pair": f"{base.get('symbol','?')}/{quote.get('symbol','?')}",
            "base_token": {"address": base.get("address"), "symbol": base.get("symbol"), "name": base.get("name")},
            "quote_token": {"address": quote.get("address"), "symbol": quote.get("symbol"), "name": quote.get("name")},
            "base_token_price_usd": _float(attrs.get("base_token_price_usd")),
            "quote_token_price_usd": _float(attrs.get("quote_token_price_usd")),
            "price_change_percentage": attrs.get("price_change_percentage") or {},
            "tvl_usd": _float(attrs.get("reserve_in_usd")),
            "volume_24h_usd": _float(vols.get("h24")),
            "volume_6h_usd": _float(vols.get("h6")),
            "volume_1h_usd": _float(vols.get("h1")),
            "transactions": txs,
            "pool_created_at": attrs.get("pool_created_at"),
            "fdv_usd": _float(attrs.get("fdv_usd")),
            "market_cap_usd": _float(attrs.get("market_cap_usd")),
            "source": "GECKOTERMINAL",
            "source_updated_at": time.time(),
        }

    def network_pools(self, chain_key: str, page: int = 1) -> list[dict]:
        cfg = chain_config(chain_key)
        payload = self._get(f"/networks/{cfg.gecko_network}/pools", {"page": page, "include": "base_token,quote_token"})
        return [self._normalise_pool(row, payload, cfg.key) for row in payload.get("data") or []]

    def pool(self, chain_key: str, address: str) -> dict:
        cfg = chain_config(chain_key)
        payload = self._get(f"/networks/{cfg.gecko_network}/pools/{address}", {"include": "base_token,quote_token"})
        row = payload.get("data")
        if not isinstance(row, dict):
            raise MarketDataError("Pool not found")
        return self._normalise_pool(row, payload, cfg.key)

    def ohlcv(self, chain_key: str, address: str, *, timeframe: str = "hour", limit: int = 168, before_timestamp: int | None = None) -> list[dict]:
        cfg = chain_config(chain_key)
        params: dict[str, Any] = {"aggregate": 1, "limit": min(1000, max(1, int(limit))), "currency": "usd", "token": "base"}
        if before_timestamp:
            params["before_timestamp"] = int(before_timestamp)
        payload = self._get(f"/networks/{cfg.gecko_network}/pools/{address}/ohlcv/{timeframe}", params)
        rows = ((((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or [])
        candles = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6: continue
            candles.append({"timestamp": int(row[0]), "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
        candles.sort(key=lambda c: c["timestamp"])
        return candles

    def ohlcv_days(self, chain_key: str, address: str, days: int, *, timeframe: str = "hour") -> list[dict]:
        target = max(1, int(days)) * (24 if timeframe == "hour" else 1)
        target = min(target, 24 * 365)
        rows: dict[int, dict] = {}
        before = None
        while len(rows) < target:
            batch = self.ohlcv(chain_key, address, timeframe=timeframe, limit=min(1000, target - len(rows)), before_timestamp=before)
            if not batch:
                break
            for c in batch:
                rows[int(c["timestamp"])] = c
            oldest = min(int(c["timestamp"]) for c in batch)
            if before is not None and oldest >= before:
                break
            before = oldest - 1
            if len(batch) < min(1000, target - len(rows) + len(batch)):
                break
        return [rows[k] for k in sorted(rows)][-target:]


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
