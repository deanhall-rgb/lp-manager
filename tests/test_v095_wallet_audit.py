from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lp_manager.db import Store
import lp_manager.wallet_audit as wallet_audit
from lp_manager.chain_registry import chain_config
from lp_manager.wallet_audit import (
    _blockscout_transactions,
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
    store.set_setting("wallet_audit:last_scan",{
        "read_at":1_790_000_100.0,
        "coverage_complete":True,
        "chains":[{"chain":"ROBINHOOD_CHAIN","transfers":1,"transactions":1,"full_transaction_history":True}],
    })
    store.upsert_wallet_audit_transaction({
        "id":"wat:test","chain":"ROBINHOOD_CHAIN","tx_hash":"0xfund","occurred_at":1_790_000_000.0,
        "direction":"IN","kind":"NATIVE_TRANSFER","method":"","from_address":"0x2","to_address":"0x1",
        "native_symbol":"ETH","value_native":0.2,"gas_native":0.0,"success":True,"source":"TEST","payload":{},
    })
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


def test_blockscout_full_history_captures_zero_value_approval_and_gas(monkeypatch):
    wallet="0x1111111111111111111111111111111111111111"
    fake=[{
        "hash":"0xapprove",
        "timestamp":"2026-09-01T12:00:00Z",
        "from":{"hash":wallet},
        "to":{"hash":"0x2222222222222222222222222222222222222222","name":"Example Token"},
        "value":"0",
        "status":"ok",
        "method":"approve",
        "raw_input":"0x095ea7b3",
        "gas_used":"46000",
        "gas_price":"1000000000",
        "fee":{"type":"actual","value":"46000000000000"},
    }]
    monkeypatch.setattr(wallet_audit,"_blockscout_pages",lambda cfg,path,max_pages=40:fake)
    rows=_blockscout_transactions(chain_config("ETHEREUM"),wallet,{})
    assert len(rows)==1
    row=rows[0]
    assert row["direction"]=="OUT"
    assert row["kind"]=="APPROVAL"
    assert row["value_native"]==0.0
    assert abs(row["gas_native"]-0.000046)<1e-12
    assert row["method"]=="approve"


def test_wallet_audit_summary_includes_full_transactions_gas_and_transfer_context(tmp_path):
    store=Store(tmp_path/"full.sqlite3")
    transfer=_event("fund","0xfund","IN",0.2)
    store.upsert_wallet_audit_event(transfer)
    store.upsert_wallet_audit_transaction({
        "id":"wat:fund","chain":"ROBINHOOD_CHAIN","tx_hash":"0xfund","occurred_at":1_790_000_000.0,
        "direction":"OUT","kind":"BRIDGE","method":"bridge","from_address":"0x1","to_address":"0x2",
        "to_name":"Bridge","native_symbol":"ETH","value_native":0.2,"gas_native":0.001,
        "success":True,"source":"TEST_FULL_HISTORY","payload":{},
    })
    store.set_setting("wallet_audit:last_scan",{
        "read_at":1_790_000_100.0,
        "coverage_complete":True,
        "chains":[{"chain":"ROBINHOOD_CHAIN","transfers":1,"transactions":1,"full_transaction_history":True}],
    })
    summary=wallet_audit_summary(_settings(),store)
    assert summary["metrics"]["observed_transactions"]==1
    assert summary["metrics"]["observed_transfers"]==1
    assert summary["metrics"]["full_history_chains"]==1
    assert summary["unresolved"][0]["transaction"]["kind"]=="BRIDGE"
    assert summary["gas_by_chain"][0]["gas_native"]==0.001
    assert summary["activity"][0]["kind"]=="BRIDGE"
    assert summary["activity"][0]["transfers"][0]["asset"]=="ETH"


def test_v095_wallet_audit_ui_is_separate_and_manual_review_is_explicit():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="walletaudit"' in html
    assert 'id="walletaudit-section"' in html
    assert "Wallet Audit & Capital Ledger" in html
    assert 'id="wallet-audit-scan-btn"' in html
    assert "Funding gaps to review" in html
    assert "Full on-chain activity" in html
    assert "Scan coverage & transaction costs" in html
    assert "function renderWalletAudit()" in js
    assert "New money / funding" in js
    assert "Transfer from/to one of my wallets or a bridge" in js
    assert "enter what it actually cost you in cash" in js
    assert "/api/wallet-audit/scan" in js
    assert "/api/wallet-audit/resolve" in js
    assert "$('[data-wallet-audit-review]').forEach" in js
    assert "$('[data-wallet-audit-activity]').forEach" in js
    assert "function showWalletAuditActivity" in js
    assert "Scanning full history" in js
