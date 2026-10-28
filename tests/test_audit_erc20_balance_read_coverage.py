"""Fail closed on unreadable ERC20 balances; never confuse an RPC error with zero.

All tests use offline Web3 doubles. No signing, transactions, or provider access.
"""
from types import SimpleNamespace

import pytest

from lp_manager import wallet, wallet_audit
from lp_manager.db import Store


WALLET = "0x1111111111111111111111111111111111111111"
TOKEN = "0x2222222222222222222222222222222222222222"


class _Chain:
    key = "BASE"
    name = "Base"
    native_symbol = "ETH"
    wrapped_native = None

    def enabled(self):
        return True

    def rpc_url(self):
        return "https://unused.invalid"

    def rpc_source(self):
        return "TEST"


def _web3(*, failure=None, balance=0):
    def token_balance():
        if failure == "balance":
            raise RuntimeError("ERC20 RPC timeout")
        return balance

    def symbol():
        if failure == "metadata":
            raise RuntimeError("symbol unavailable")
        return "TKN"

    class Eth:
        def get_balance(self, _address):
            return 0

        def contract(self, **_kwargs):
            if failure == "contract":
                raise RuntimeError("contract RPC unavailable")
            return SimpleNamespace(functions=SimpleNamespace(
                balanceOf=lambda _address: SimpleNamespace(call=token_balance),
                symbol=lambda: SimpleNamespace(call=symbol),
                decimals=lambda: SimpleNamespace(call=lambda: 18),
            ))

    return SimpleNamespace(eth=Eth(), from_wei=lambda value, _unit: value / 10**18)


def _offline(monkeypatch, *, failure=None, balance=0):
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    monkeypatch.setattr(wallet, "CHAINS", {"BASE": _Chain()})
    monkeypatch.setattr(wallet, "build_read_only_web3", lambda _url: _web3(failure=failure, balance=balance))
    monkeypatch.setattr(wallet, "_known_tokens_from_store", lambda _store, _chain: {TOKEN})
    monkeypatch.setattr(wallet, "_alchemy_token_addresses", lambda _cfg, _wallet: set())
    monkeypatch.setattr(wallet, "_blockscout_token_addresses", lambda _chain, _wallet: set())


@pytest.mark.parametrize("failure", ["contract", "balance"])
def test_failed_known_token_read_does_not_certify_portfolio(tmp_path, monkeypatch, failure):
    store = Store(tmp_path / "wallet.sqlite3")
    _offline(monkeypatch, failure=failure)
    settings = SimpleNamespace(wallet_address=WALLET, currency="USD")

    snapshot = wallet.build_wallet_snapshot(settings, store, include_health=False)

    assert snapshot["ok"] is False
    assert snapshot["coverage_complete"] is False
    assert snapshot["chains"][0]["status"] == "ERROR"
    assert snapshot["chains"][0]["ok"] is False
    assert "ERC20 balance read failed" in snapshot["chains"][0]["wallet_error"]
    assert wallet_audit.wallet_audit_summary(settings, store)["metrics"]["valuation_complete"] is False


def test_known_token_with_genuine_zero_balance_preserves_coverage(tmp_path, monkeypatch):
    store = Store(tmp_path / "zero.sqlite3")
    _offline(monkeypatch, balance=0)
    snapshot = wallet.build_wallet_snapshot(
        SimpleNamespace(wallet_address=WALLET), store, include_health=False
    )
    assert snapshot["ok"] is True
    assert snapshot["coverage_complete"] is True
    assert snapshot["holdings"] == []
    assert snapshot["hidden_holdings"] == []


def test_unavailable_symbol_metadata_does_not_discard_verified_balance(monkeypatch):
    _offline(monkeypatch, failure="metadata", balance=10**18)
    row = wallet._token_holding(_web3(failure="metadata", balance=10**18), WALLET, "BASE", TOKEN)
    assert row is not None
    assert row["balance"] == 1.0
    assert row["symbol"] == "TOKEN"
