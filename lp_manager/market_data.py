from __future__ import annotations

import time
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from .chain_registry import chain_config


class MarketDataError(RuntimeError):
    pass


class GeckoTerminalClient:
    BASE = "https://api.geckoterminal.com/api/v2"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json;version=20230203", "User-Agent": "LP-Manager/0.8.4"})
        self._cache: dict[str, tuple[float, dict]] = {}
        self._last_request_at = 0.0
        # Public GeckoTerminal is rate limited and cached upstream. A small client-side
        # gap plus batching prevents Wallet refreshes from starving Scout/Replay.
        self._min_request_gap = 0.35

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{self.BASE}{path}"
        cache_key = url + "?" + "&".join(f"{k}={v}" for k,v in sorted((params or {}).items()))
        cached = self._cache.get(cache_key)
        is_ohlcv = "/ohlcv/" in path
        cache_ttl = 5 * 60 if is_ohlcv else 20
        if cached and time.time() - cached[0] < cache_ttl:
            return cached[1]
        last: Exception | None = None
        attempts = 2 if is_ohlcv else 4
        timeout_seconds = 12 if is_ohlcv else 25
        for attempt in range(attempts):
            try:
                wait = self._min_request_gap - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    time.sleep(wait)
                r = self.session.get(url, params=params or {}, timeout=timeout_seconds)
                self._last_request_at = time.monotonic()
                if r.status_code == 429:
                    retry = r.headers.get("Retry-After")
                    try:
                        delay = max(1.0, min(15.0, float(retry))) if retry else min(8.0, 2.0 * (attempt + 1))
                    except Exception:
                        delay = min(8.0, 2.0 * (attempt + 1))
                    last = MarketDataError(f"GeckoTerminal rate limited (429); retrying in {delay:.1f}s")
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                payload = r.json()
                self._cache[cache_key] = (time.time(), payload)
                return payload
            except (requests.RequestException, ValueError, MarketDataError) as exc:
                last = exc
                if attempt < attempts - 1:
                    time.sleep(min(4.0, 0.5 * (2 ** attempt)))
        # Prefer slightly stale market data over turning the whole Scout into a 502.
        # Callers can still inspect source_updated_at on normalised rows.
        stale = self._cache.get(cache_key)
        stale_limit = 60 * 60 if is_ohlcv else 15 * 60
        if stale and time.time() - stale[0] < stale_limit:
            payload = dict(stale[1])
            payload["_lp_manager_cache_status"] = "STALE_FALLBACK"
            payload["_lp_manager_cache_age_seconds"] = round(time.time() - stale[0], 1)
            return payload
        raise MarketDataError(f"GeckoTerminal request failed after retries: {last or 'unknown provider error'}")

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



    def _alchemy_token_prices(self, chain_key: str, addresses: list[str]) -> dict[str, float]:
        """Prefer the configured Alchemy Prices API for current token marks.

        This keeps the small GeckoTerminal public request budget available for
        pool discovery/OHLC rather than spending it on wallet valuation.
        """
        api_key=os.getenv("ALCHEMY_API_KEY", "").strip()
        if not api_key or not addresses:
            return {}
        cfg=chain_config(chain_key)
        network=str(cfg.alchemy_slug or "").strip()
        if not network:
            return {}
        unique=[]
        for address in addresses:
            a=str(address or "").lower()
            if a and a not in unique:
                unique.append(a)
        out: dict[str,float]={}
        for start in range(0,len(unique),25):
            batch=unique[start:start+25]
            try:
                r=self.session.post(
                    f"https://api.g.alchemy.com/prices/v1/{api_key}/tokens/by-address",
                    json={"addresses":[{"network":network,"address":a} for a in batch]},
                    timeout=15,
                    headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"LP-Manager/0.8.4"},
                )
                r.raise_for_status(); payload=r.json()
                for row in payload.get("data") or []:
                    address=str(row.get("address") or "").lower()
                    price=0.0
                    for pr in row.get("prices") or []:
                        if str(pr.get("currency") or "").lower()=="usd":
                            price=_float(pr.get("value")); break
                    if address and price>0: out[address]=price
            except Exception:
                return {}
        return out

    def _alchemy_historical_token(self, chain_key: str, token: dict[str, Any], *, days: int, timeframe: str) -> list[dict[str, Any]]:
        """Historical token price points from Alchemy, used when pool OHLC is rate limited."""
        api_key=os.getenv("ALCHEMY_API_KEY", "").strip()
        if not api_key:
            return []
        cfg=chain_config(chain_key)
        end=datetime.now(timezone.utc)
        start=end-timedelta(days=max(1,int(days)))
        interval="1h" if str(timeframe).lower()=="hour" else "1d"
        identity={}
        address=str(token.get("address") or "").strip()
        symbol=str(token.get("symbol") or "").strip()
        if address and cfg.alchemy_slug:
            identity={"network":cfg.alchemy_slug,"address":address}
        elif symbol:
            identity={"symbol":symbol}
        else:
            return []
        body={**identity,"startTime":start.isoformat().replace("+00:00","Z"),"endTime":end.isoformat().replace("+00:00","Z"),"interval":interval,"withMarketData":True}
        url=f"https://api.g.alchemy.com/prices/v1/{api_key}/tokens/historical"
        try:
            r=self.session.post(url,json=body,timeout=20,headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"LP-Manager/0.8.4"})
            if r.status_code==404 and symbol and "symbol" not in identity:
                body={"symbol":symbol,"startTime":body["startTime"],"endTime":body["endTime"],"interval":interval,"withMarketData":True}
                r=self.session.post(url,json=body,timeout=20,headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"LP-Manager/0.8.4"})
            r.raise_for_status(); payload=r.json()
        except Exception:
            return []
        rows=[]
        for row in payload.get("data") or []:
            try:
                stamp=str(row.get("timestamp") or "").replace("Z","+00:00")
                ts=int(datetime.fromisoformat(stamp).timestamp())
                value=_float(row.get("value"))
                if ts>0 and value>0:
                    rows.append({"timestamp":ts,"price_usd":value,"market_cap":_float(row.get("marketCap")),"total_volume":_float(row.get("totalVolume"))})
            except Exception:
                continue
        rows.sort(key=lambda x:x["timestamp"]); return rows

    def alchemy_pool_history(self, chain_key: str, onchain: dict[str, Any], days: int, *, timeframe: str="hour") -> list[dict[str, Any]]:
        """Construct a deterministic pair-price series from Alchemy token histories.

        Alchemy provides price points rather than pool OHLC. We therefore use
        zero-span candles (O=H=L=C) and volume=0. Range durability remains
        meaningful from observed closes, while fee economics continues to use
        pool-specific volume only when that evidence is separately available.
        """
        if not isinstance(onchain,dict) or not onchain.get("ok"):
            return []
        t0=dict(onchain.get("token0") or {}); t1=dict(onchain.get("token1") or {})
        h0=self._alchemy_historical_token(chain_key,t0,days=days,timeframe=timeframe)
        h1=self._alchemy_historical_token(chain_key,t1,days=days,timeframe=timeframe)
        symbols={str(t0.get("symbol") or "").upper():h0,str(t1.get("symbol") or "").upper():h1}
        unit_label=str((onchain.get("price_lens") or {}).get("unit_label") or "")
        if " per " not in unit_label:
            return []
        quote_symbol,base_symbol=[x.strip().upper() for x in unit_label.split(" per ",1)]
        base_rows=symbols.get(base_symbol) or []; quote_rows=symbols.get(quote_symbol) or []
        # Dollar-pegged quotes may occasionally be absent from the historical API.
        stable={"USDC","USDT","USDG","DAI","USDS","USDBC","FRAX","GHO"}
        step=3600 if str(timeframe).lower()=="hour" else 86400
        def keyed(rows):
            return {int(r["timestamp"]//step)*step:r for r in rows}
        kb=keyed(base_rows); kq=keyed(quote_rows)
        if not kb and base_symbol in stable and kq:
            kb={k:{"timestamp":k,"price_usd":1.0} for k in kq}
        if not kq and quote_symbol in stable and kb:
            kq={k:{"timestamp":k,"price_usd":1.0} for k in kb}
        stamps=sorted(set(kb).intersection(kq))
        out=[]
        for ts in stamps:
            bp=_float(kb[ts].get("price_usd")); qp=_float(kq[ts].get("price_usd"))
            if bp<=0 or qp<=0: continue
            close=bp/qp
            out.append({"timestamp":ts,"open":close,"high":close,"low":close,"close":close,"volume":0.0,"source":"ALCHEMY_TOKEN_PRICE_POINTS"})
        target=max(1,int(days))*(24 if str(timeframe).lower()=="hour" else 1)
        return out[-target:]

    def token_prices(self, chain_key: str, addresses: list[str]) -> dict[str, float]:
        """Fetch up to 30 token prices in one public-api request.

        Wallet discovery used to make one GeckoTerminal call per token, which could
        consume the whole public rate budget and make Scout return 502s.
        """
        cfg = chain_config(chain_key)
        unique=[]
        for address in addresses:
            a=str(address or "").lower()
            if a and a not in unique:
                unique.append(a)
        alchemy=self._alchemy_token_prices(chain_key, unique)
        if alchemy:
            return alchemy
        out: dict[str,float] = {}
        for start in range(0, len(unique), 30):
            batch=unique[start:start+30]
            if not batch:
                continue
            payload=self._get(f"/simple/networks/{cfg.gecko_network}/token_price/{','.join(batch)}")
            attrs=((payload.get("data") or {}).get("attributes") or {})
            prices=attrs.get("token_prices") or {}
            for address,value in prices.items():
                try: out[str(address).lower()] = float(value or 0)
                except Exception: out[str(address).lower()] = 0.0
        return out

    def token(self, chain_key: str, address: str) -> dict:
        cfg = chain_config(chain_key)
        payload = self._get(f"/networks/{cfg.gecko_network}/tokens/{address}")
        row = payload.get("data") or {}
        attrs = row.get("attributes") or {}
        return {
            "chain": cfg.key,
            "address": attrs.get("address") or address,
            "name": attrs.get("name"),
            "symbol": attrs.get("symbol"),
            "price_usd": _float(attrs.get("price_usd")),
            "fdv_usd": _float(attrs.get("fdv_usd")),
            "market_cap_usd": _float(attrs.get("market_cap_usd")),
            "volume_24h_usd": _float(attrs.get("volume_usd", {}).get("h24") if isinstance(attrs.get("volume_usd"), dict) else 0),
            "source": "GECKOTERMINAL",
        }

    def network_pools(self, chain_key: str, page: int = 1) -> list[dict]:
        cfg = chain_config(chain_key)
        payload = self._get(f"/networks/{cfg.gecko_network}/pools", {"page": page, "include": "base_token,quote_token"})
        return [self._normalise_pool(row, payload, cfg.key) for row in payload.get("data") or []]

    def token_pools(self, chain_key: str, token_address: str, page: int = 1) -> list[dict]:
        """Pools containing a specific token.

        Network top-pool pages are intentionally not an exhaustive discovery
        universe. Wallet/watchlist token expansion makes assets such as DELTA,
        PONS and HOOKR discoverable even when they are not in the network's top
        page at this moment.
        """
        cfg = chain_config(chain_key)
        payload = self._get(
            f"/networks/{cfg.gecko_network}/tokens/{token_address}/pools",
            {"page": page, "include": "base_token,quote_token"},
        )
        return [self._normalise_pool(row, payload, cfg.key) for row in payload.get("data") or []]

    def pool(self, chain_key: str, address: str) -> dict:
        cfg = chain_config(chain_key)
        payload = self._get(f"/networks/{cfg.gecko_network}/pools/{address}", {"include": "base_token,quote_token"})
        row = payload.get("data")
        if not isinstance(row, dict):
            raise MarketDataError("Pool not found")
        return self._normalise_pool(row, payload, cfg.key)


    def resolve_pool(self, preferred_chain: str, address: str) -> tuple[str, dict]:
        """Resolve a pool without multiplying transient provider failures.

        Cross-chain probing is only appropriate for a genuine not-found response.
        Rate limits/timeouts are re-raised immediately so callers can use cache or
        Alchemy/on-chain fallbacks instead of consuming the remaining quota.
        """
        from .chain_registry import CHAINS
        preferred=str(preferred_chain).upper()
        try:
            return preferred,self.pool(preferred,address)
        except Exception as exc:
            msg=str(exc).lower()
            transient=any(x in msg for x in ("429","rate limit","timeout","temporarily","connection"))
            if transient:
                raise
            first=exc
        errors=[f"{preferred}: {first}"]
        for key in CHAINS:
            if key==preferred: continue
            try:
                return key,self.pool(key,address)
            except Exception as exc:
                msg=str(exc).lower()
                if any(x in msg for x in ("429","rate limit","timeout","temporarily","connection")):
                    raise
                errors.append(f"{key}: {exc}")
        raise MarketDataError("Pool address was not found on supported chains. " + "; ".join(errors[:3]))

    def ohlcv(self, chain_key: str, address: str, *, timeframe: str = "hour", limit: int = 168, before_timestamp: int | None = None, token: str = "base") -> list[dict]:
        cfg = chain_config(chain_key)
        token = "quote" if str(token).lower() == "quote" else "base"
        params: dict[str, Any] = {"aggregate": 1, "limit": min(1000, max(1, int(limit))), "currency": "usd", "token": token}
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

    def ohlcv_days(self, chain_key: str, address: str, days: int, *, timeframe: str = "hour", token: str = "base") -> list[dict]:
        target = max(1, int(days)) * (24 if timeframe == "hour" else 1)
        target = min(target, 24 * 365)
        rows: dict[int, dict] = {}
        before = None
        while len(rows) < target:
            batch = self.ohlcv(chain_key, address, timeframe=timeframe, limit=min(1000, target - len(rows)), before_timestamp=before, token=token)
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

    def ohlcv_window(self, chain_key: str, address: str, start_timestamp: int, end_timestamp: int, *, timeframe: str = "hour", token: str = "base", max_candles: int = 3000) -> list[dict]:
        """Fetch a bounded historical window, including campaigns that ended in the past."""
        start=int(start_timestamp); end=int(end_timestamp)
        if end <= start:
            return []
        rows: dict[int,dict] = {}
        before=end+1
        while len(rows) < max_candles:
            batch=self.ohlcv(chain_key,address,timeframe=timeframe,limit=min(1000,max_candles-len(rows)),before_timestamp=before,token=token)
            if not batch:
                break
            for c in batch:
                ts=int(c.get("timestamp") or 0)
                if start <= ts <= end:
                    rows[ts]=c
            oldest=min(int(c.get("timestamp") or 0) for c in batch)
            if oldest <= start or oldest >= before:
                break
            before=oldest-1
        return [rows[k] for k in sorted(rows)]


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
