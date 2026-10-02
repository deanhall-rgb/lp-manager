from pathlib import Path
import time

import lp_manager.profit_engine as profit_engine
from lp_manager.strategy_lab import _pool_from_onchain


class FakeStore:
    def __init__(self):
        self.settings = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value


def _onchain_wpol_usdt0(address: str):
    return {
        "ok": True,
        "chain": "POLYGON",
        "pool_address": address,
        "token0": {"address": "0x" + "1" * 40, "symbol": "WPOL", "decimals": 18},
        "token1": {"address": "0x" + "2" * 40, "symbol": "USDT0", "decimals": 6},
        "fee_tier": 10000,
        "fee_tier_bps": 100.0,
        "tick_spacing": 200,
        "price_lens": {
            "current": 0.112,
            "lower": 0.112,
            "upper": 0.112,
            "unit": "USDT0_PER_WPOL",
            "unit_label": "USDT0 per WPOL",
            "token0_symbol": "WPOL",
            "token1_symbol": "USDT0",
            "canonical_unit": "USDT0_PER_WPOL",
            "canonical_current": 0.112,
            "inverted": False,
        },
    }


def _candles(count=72, close=0.112):
    now = int(time.time())
    start = now - (count - 1) * 3600
    return [
        {
            "timestamp": start + i * 3600,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 0.0,
        }
        for i in range(count)
    ]


def test_profit_history_uses_fresh_persisted_cache_before_provider_calls(monkeypatch):
    address = "0x" + "a" * 40
    store = FakeStore()
    store.settings[f"profit:history:v09642:POLYGON:{address}:hour"] = {
        "saved_at": time.time(),
        "provider": "TEST_VALIDATED_CACHE",
        "candles": _candles(),
    }

    class Market:
        def alchemy_pool_history(self, *args, **kwargs):
            raise AssertionError("Alchemy should not be called when fresh history cache is usable")

        def ohlcv_days(self, *args, **kwargs):
            raise AssertionError("Gecko OHLC should not be called when fresh history cache is usable")

    monkeypatch.setattr(profit_engine, "read_v3_pool_metadata", lambda *a, **k: _onchain_wpol_usdt0(address))
    monkeypatch.setattr(profit_engine, "read_v3_observation_history", lambda *a, **k: [])

    pool_fallback = {
        "chain": "POLYGON",
        "pool_address": address,
        "pair": "WPOL/USDT0",
        "base_token": {"symbol": "WPOL"},
        "quote_token": {"symbol": "USDT0"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 2_000_000,
    }

    _, _, candles, provider, warning = profit_engine._load_pool_and_history(
        Market(), "POLYGON", address, 30, pool_fallback=pool_fallback, store=store
    )

    assert len(candles) == 72
    assert provider == "TEST_VALIDATED_CACHE"
    assert "before provider lookup" in warning


def test_usdt0_is_treated_as_stable_and_can_use_pool_ohlcv_fallback(monkeypatch):
    address = "0x" + "b" * 40
    calls = []

    class Market:
        def alchemy_pool_history(self, *args, **kwargs):
            return []

        def ohlcv_days(self, chain, pool, days, **kwargs):
            calls.append((chain, pool, days, kwargs.get("token")))
            return _candles(72)

    monkeypatch.setattr(profit_engine, "read_v3_pool_metadata", lambda *a, **k: _onchain_wpol_usdt0(address))
    monkeypatch.setattr(profit_engine, "read_v3_observation_history", lambda *a, **k: [])

    pool_fallback = {
        "chain": "POLYGON",
        "pool_address": address,
        "pair": "WPOL/USDT0",
        "base_token": {"symbol": "WPOL"},
        "quote_token": {"symbol": "USDT0"},
        "tvl_usd": 1_000_000,
        "volume_24h_usd": 2_000_000,
    }

    _, _, candles, _, warning = profit_engine._load_pool_and_history(
        Market(), "POLYGON", address, 30, pool_fallback=pool_fallback, store=FakeStore()
    )

    assert len(candles) == 72
    assert calls
    assert "token-USD OHLC rejected for non-stable execution pair" not in str(warning or "")


def test_onchain_pool_reconstruction_treats_usdt0_as_usd_stable():
    address = "0x" + "c" * 40
    pool = _pool_from_onchain("POLYGON", address, _onchain_wpol_usdt0(address), {})
    assert pool["pair"] == "WPOL/USDT0"
    assert pool["base_token_price_usd"] == 0.112
    assert pool["quote_token_price_usd"] == 1.0


def test_profit_lab_reuses_shared_candidate_context_instead_of_relooking_up_pool():
    root = Path(__file__).parents[1]
    api = (root / "lp_manager" / "api.py").read_text(encoding="utf-8")

    assert "def _profit_pool_fallback" in api
    assert "candidate=enriched" in api
    assert 'evaluation.get("quick_economics")' in api
    assert "pool_fallback=_profit_pool_fallback(chain,address)" in api
    assert "SHARED_CANDIDATE_UNIVERSE_CACHE" in api
