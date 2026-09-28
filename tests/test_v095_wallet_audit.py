from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lp_manager.db import Store
from lp_manager.wallet_audit import (
    _classify_scan_rows,
    resolve_wallet_audit_event,
    wallet_audit_summary,
)


def _settings():
    return SimpleNamespace(wallet_address="0x1111111111111111111111111111111111111111", currency="USD")


def _event(event_id: str, tx_hash: str, direction: str, amount: float = 1.0):
    return {
        "id":event_id,
        "chain":"ROBINHOOD_CHAIN",
        "tx_hash":tx_hash,
        "transfer_key":event_id,
        "occurred_at":1_790_000_000.0,
        "direction":direction,
        "category":"external",
        "asset":"ETH",
        "token_address":"",
        "amount":amount,
        "from_address":"0x2222222222222222222222222222222222222222",
        "to_address":"0x1111111111111111111111111111111111111111",
        "source":"TEST",
        "payload":{},
    }


def test_wallet_audit_only_flags_one_sided_unknown_transfers(tmp_path):
    store=Store(tmp_path/"audit.sqlite3")
    store.record_financial_event(
        position_id=None,event_type="OPEN_POSITION",chain="ROBINHOOD_CHAIN",
        tx_hash="0xknown",amount_usd=0,
    )
    rows=[
        _event("a","0xfunding","IN"),
        _event("b","0xswap","OUT"),
        _event("c","0xswap","IN"),
        _event("d","0xknown","OUT"),
    ]
    classified=_classify_scan_rows(store,rows)
    status={row["id"]:row["review_status"] for row in classified}
    assert status["a"]=="UNRESOLVED"
    assert status["b"]=="INTERNAL_CONVERSION"
    assert status["c"]=="INTERNAL_CONVERSION"
    assert status["d"]=="INTERNAL_PROTOCOL"


def test_manual_funding_cost_drives_true_cash_pnl(tmp_path):
    store=Store(tmp_path/"capital.sqlite3")
    store.save_wallet_snapshot({
        "wallet_liquid_value_usd":400.0,
        "lp_value_usd":550.0,
        "total_tracked_value_usd":950.0,
    })
    row=store.upsert_wallet_audit_event(_event("fund","0xfund","IN",0.2))
    assert row["review_status"]=="UNRESOLVED"

    resolve_wallet_audit_event(
        _settings(),store,"fund",
        classification="CONFIRMED_FUNDING",
        fiat_amount=1000.0,
        note="Original cash paid including provider fees",
    )
    summary=wallet_audit_summary(_settings(),store)
    assert summary["metrics"]["confirmed_contributions"]==1000.0
    assert summary["metrics"]["confirmed_withdrawals"]==0.0
    assert summary["metrics"]["current_portfolio_value"]==950.0
    assert summary["metrics"]["provisional_true_pnl"]==-50.0
    assert summary["metrics"]["unresolved_count"]==0
    assert summary["metrics"]["pnl_complete"] is True


def test_rescan_preserves_human_wallet_audit_classification(tmp_path):
    store=Store(tmp_path/"preserve.sqlite3")
    store.upsert_wallet_audit_event(_event("fund","0xfund","IN",0.2))
    resolve_wallet_audit_event(
        _settings(),store,"fund",
        classification="INTERNAL_TRANSFER",
        fiat_amount=None,
        note="Moved from my other wallet",
    )
    updated=_event("fund","0xfund","IN",0.25)
    updated["source"]="RESCAN"
    row=store.upsert_wallet_audit_event(updated)
    assert row["review_status"]=="INTERNAL_TRANSFER"
    assert row["note"]=="Moved from my other wallet"
    assert row["amount"]==0.25


def test_v095_wallet_audit_ui_is_separate_and_manual_review_is_explicit():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="walletaudit"' in html
    assert 'id="walletaudit-section"' in html
    assert "Wallet Audit & Capital Ledger" in html
    assert 'id="wallet-audit-scan-btn"' in html
    assert "Funding gaps to review" in html
    assert "function renderWalletAudit()" in js
    assert "New money / funding" in js
    assert "Transfer from/to one of my wallets or a bridge" in js
    assert "enter what it actually cost you in cash" in js
    assert "/api/wallet-audit/scan" in js
    assert "/api/wallet-audit/resolve" in js
