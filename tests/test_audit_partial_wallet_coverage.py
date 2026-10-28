"""Wallet snapshot coverage must not masquerade as verified lifetime equity.

All tests are offline. No provider requests, wallet signing, or transfers.
"""

from types import SimpleNamespace

import pytest

from lp_manager import wallet, wallet_audit
from lp_manager.db import Store


ADDRESS = "0x1111111111111111111111111111111111111111"


def _settings():
    return SimpleNamespace(wallet_address=ADDRESS, currency="USD")


class _Config:
    key = "BASE"
    name = "Base"
    native_symbol = "ETH"
    wrapped_native = None

    def __init__(self, *, enabled=True, url="https://unused.invalid"):
        self._enabled = enabled
        self._url = url

    def enabled(self):
        return self._enabled

    def rpc_url(self):
        return self._url

    def rpc_source(self):
        return "TEST"


def _offline_wallet(monkeypatch, config):
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    monkeypatch.setattr(wallet, "CHAINS", {"BASE": config})


def test_wallet_rpc_exception_marks_partial_snapshot_not_success(tmp_path, monkeypatch):
    store = Store(tmp_path / "rpc.sqlite3")
    _offline_wallet(monkeypatch, _Config())

    def broken_rpc(_url):
        raise RuntimeError("offline RPC timeout")

    monkeypatch.setattr(wallet, "build_read_only_web3", broken_rpc)
    snapshot = wallet.build_wallet_snapshot(_settings(), store, include_health=False)
    assert snapshot["ok"] is False
    assert snapshot["coverage_complete"] is False
    assert snapshot["chains"][0]["ok"] is False
    assert snapshot["chains"][0]["status"] == "ERROR"
    assert "offline RPC timeout" in snapshot["chains"][0]["wallet_error"]
    assert store.get_wallet_snapshot()["coverage_complete"] is False
    assert wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]["valuation_complete"] is False


def test_enabled_chain_missing_rpc_is_not_complete_coverage(tmp_path, monkeypatch):
    store = Store(tmp_path / "missing.sqlite3")
    _offline_wallet(monkeypatch, _Config(url=""))
    snapshot = wallet.build_wallet_snapshot(_settings(), store, include_health=False)
    assert snapshot["ok"] is False
    assert snapshot["chains"][0]["status"] == "MISSING"


def test_disabled_only_chain_is_not_verified_zero_wealth(tmp_path, monkeypatch):
    store = Store(tmp_path / "disabled.sqlite3")
    _offline_wallet(monkeypatch, _Config(enabled=False))
    snapshot = wallet.build_wallet_snapshot(_settings(), store, include_health=False)
    assert snapshot["ok"] is False
    assert snapshot["coverage_complete"] is False


def test_successful_zero_balance_rpc_remains_valid_zero(tmp_path, monkeypatch):
    store = Store(tmp_path / "zero.sqlite3")
    _offline_wallet(monkeypatch, _Config())
    class FakeEth:
        @staticmethod
        def get_balance(_address):
            return 0
    class FakeWeb3:
        eth = FakeEth()

        @staticmethod
        def from_wei(value, _unit):
            return value / 10**18

    monkeypatch.setattr(wallet, "build_read_only_web3", lambda _url: FakeWeb3())
    snapshot = wallet.build_wallet_snapshot(_settings(), store, include_health=False)
    assert snapshot["ok"] is True
    assert snapshot["coverage_complete"] is True
    assert snapshot["total_tracked_value_usd"] == 0.0
    assert wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]["valuation_complete"] is True


@pytest.mark.parametrize("chain", [
    {"chain": "BASE", "status": "CONFIGURED", "ok": True, "wallet_error": "RPC timed out"},
    {"chain": "BASE", "status": "ERROR", "ok": False},
    {"chain": "BASE", "status": "MISSING", "ok": False},
    {"chain": "BASE", "status": "WRONG_CHAIN", "ok": False},
])
def test_old_snapshot_marked_ok_cannot_certify_failed_chain(tmp_path, chain):
    store = Store(tmp_path / "legacy.sqlite3")
    store.save_wallet_snapshot({
        "ok": True,
        "total_tracked_value_usd": 950.0,
        "chains": [chain],
    })
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["wallet_coverage_complete"] is False
    assert metrics["valuation_complete"] is False
    assert metrics["current_portfolio_value"] is None
    assert metrics["provisional_true_pnl"] is None
    assert metrics["pnl_complete"] is False


def test_explicit_partial_coverage_flag_cannot_be_overridden_by_valid_value(tmp_path):
    store = Store(tmp_path / "partial.sqlite3")
    store.save_wallet_snapshot({
        "ok": True,
        "coverage_complete": False,
        "total_tracked_value_usd": 950.0,
    })
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["wallet_coverage_complete"] is False
    assert metrics["valuation_complete"] is False


def test_disabled_chain_does_not_invalidate_other_successful_chain(tmp_path):
    store = Store(tmp_path / "good.sqlite3")
    store.save_wallet_snapshot({
        "ok": True,
        "coverage_complete": True,
        "total_tracked_value_usd": 950.0,
        "chains": [
            {"chain": "BASE", "status": "CONFIGURED", "ok": True},
            {"chain": "OTHER", "status": "DISABLED", "ok": False},
        ],
    })
    metrics = wallet_audit.wallet_audit_summary(_settings(), store)["metrics"]
    assert metrics["wallet_coverage_complete"] is True
    assert metrics["valuation_complete"] is True
    assert metrics["current_portfolio_value"] == 950.0
