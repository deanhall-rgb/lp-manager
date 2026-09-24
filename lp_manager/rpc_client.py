from __future__ import annotations

import time
from typing import Any

import requests

try:  # Keep non-chain unit tests/imports usable when Web3 is not installed.
    from web3 import Web3
    from web3.providers.rpc import HTTPProvider
except Exception:  # pragma: no cover - exercised only in dependency-light environments.
    Web3 = None  # type: ignore[assignment]
    HTTPProvider = None  # type: ignore[assignment]


READ_ONLY_BLOCKED = {
    "eth_sendRawTransaction", "eth_sendTransaction", "eth_sign",
    "eth_signTransaction", "personal_sendTransaction", "personal_unlockAccount",
}


if HTTPProvider is not None:
    class ReadOnlyRetryProvider(HTTPProvider):
        def __init__(self, endpoint_uri: str, *args, attempts: int = 3, backoff: float = 0.35, **kwargs):
            super().__init__(endpoint_uri, *args, **kwargs)
            self.attempts = max(1, attempts)
            self.backoff = max(0.0, backoff)

        def make_request(self, method: str, params: Any):
            if method in READ_ONLY_BLOCKED:
                raise PermissionError(f"LP Manager read-only RPC blocked method {method}")
            last = None
            for attempt in range(self.attempts):
                try:
                    response = HTTPProvider.make_request(self, method, params)
                    error = response.get("error") if isinstance(response, dict) else None
                    msg = str((error or {}).get("message") or "").lower()
                    if error and (error.get("code") in {-32005, 429} or "rate limit" in msg or "too many" in msg):
                        raise RuntimeError(msg or "RPC rate limit")
                    return response
                except (requests.RequestException, RuntimeError) as exc:
                    last = exc
                    if attempt + 1 < self.attempts:
                        time.sleep(min(3.0, self.backoff * (2 ** attempt)))
            if last:
                raise last
            raise RuntimeError("RPC request failed")
else:
    class ReadOnlyRetryProvider:  # pragma: no cover
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Web3 is not installed; run pip install -r requirements.txt")


def build_read_only_web3(url: str):
    if not str(url or "").strip():
        raise ValueError("RPC URL is not configured")
    if Web3 is None or HTTPProvider is None:
        raise RuntimeError("Web3 is not installed; run pip install -r requirements.txt")
    return Web3(ReadOnlyRetryProvider(str(url).strip(), request_kwargs={"timeout": 20}))
