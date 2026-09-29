from __future__ import annotations

from pathlib import Path
import time
from types import SimpleNamespace

from lp_manager.db import Store
import lp_manager.wallet_audit as wallet_audit
from lp_manager.chain_registry import chain_config
from lp_manager.wallet_audit import (
    _blockscout_transactions,
    _classify_scan_rows,
    _classify_with_transaction_context,
    resolve_wallet_audit_event,
    wallet_audit_summary,
)


def _settings():
    return SimpleNamespace(wallet_address="0x1111111111111111111111111111111111111111", currency="USD")


def _settings_gbp():
    return SimpleNamespace(wallet_address="0x1111111111111111111111111111111111111111", currency="GBP")


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


def test_robinscan_public_history_parses_transactions_transfers_and_gas(monkeypatch):
    wallet="0x1111111111111111111111111111111111111111"
    tx_rows=[{
        "hash":"0xapprove",
        "blockNumber":123,
        "timestamp":"2026-09-01T12:00:00Z",
        "from":{"hash":wallet,"name":None},
        "to":{"hash":"0x2222222222222222222222222222222222222222","name":"DELTA"},
        "value":"0",
        "fee":"2500000000000",
        "method":"approve",
        "status":"ok",
    }]
    monkeypatch.setattr(wallet_audit,"_robinscan_pages",lambda path,max_pages=100:tx_rows)
    monkeypatch.setattr(wallet_audit,"_robinscan_tx_detail",lambda tx_hash:{
        "hash":tx_hash,
        "method":"approve",
        "tokenTransfers":[{
            "logIndex":7,
            "from":{"hash":wallet,"name":None},
            "to":{"hash":"0x3333333333333333333333333333333333333333","name":"Pool"},
            "value":"4800000000000000000000",
            "decimals":"18",
            "token":{"address_hash":"0xe8ffd7e24187f72afb08d75b1bb13088a989a791","symbol":"DELTA","name":"DELTA"},
        }],
    })
    cfg=chain_config("ROBINHOOD_CHAIN")
    txs=wallet_audit._robinscan_transactions(cfg,wallet,{})
    transfers=wallet_audit._robinscan_transfers_from_details(cfg,wallet,txs)

    assert len(txs)==1
    assert txs[0]["kind"]=="APPROVAL"
    assert txs[0]["source"]=="ROBINSCAN_PUBLIC_API"
    assert abs(txs[0]["gas_native"]-0.0000025)<1e-15
    assert len(transfers)==1
    assert transfers[0]["asset"]=="DELTA"
    assert transfers[0]["amount"]==4800.0
    assert transfers[0]["direction"]=="OUT"


def test_robinhood_scan_prefers_public_robinscan_and_marks_bridge_internal(tmp_path, monkeypatch):
    store=Store(tmp_path/"rhscan.sqlite3")
    wallet=_settings().wallet_address
    cfg=chain_config("ROBINHOOD_CHAIN")
    tx=[{
        "id":"wat:bridge","chain":"ROBINHOOD_CHAIN","tx_hash":"0xbridge","occurred_at":1_790_000_000.0,
        "direction":"OUT","kind":"BRIDGE","method":"bridge","from_address":wallet,
        "to_address":"0x4444444444444444444444444444444444444444","to_name":"Bridge",
        "native_symbol":"ETH","value_native":0.1,"gas_native":0.00001,"success":True,
        "source":"ROBINSCAN_PUBLIC_API","payload":{},
    }]
    monkeypatch.setattr(wallet_audit,"_robinscan_transactions",lambda cfg,wallet,known:tx)
    monkeypatch.setattr(wallet_audit,"_robinscan_transfers_from_details",lambda cfg,wallet,tx_rows:[])
    monkeypatch.setattr(wallet_audit,"_alchemy_url",lambda cfg:"")
    monkeypatch.setattr(wallet_audit,"_blockscout_transactions",lambda *a,**k: (_ for _ in ()).throw(AssertionError("Blockscout should not be used for Robinhood when Robinscan works")))

    result=wallet_audit.scan_wallet_audit(_settings(),store,chains=["ROBINHOOD_CHAIN"])
    row=result["chains"][0]
    assert row["full_transaction_history"] is True
    assert row["transaction_provider"]=="ROBINSCAN_PUBLIC_API"
    assert row["transfer_provider"]=="ROBINSCAN_TX_DETAIL"
    assert row["transaction_error"]==""
    assert row["transactions"]==1
    assert row["transfers"]==1

    events=store.list_wallet_audit_events(10)
    assert len(events)==1
    assert events[0]["review_status"]=="INTERNAL_TRANSFER"


def test_full_history_count_only_counts_active_chains(tmp_path):
    store=Store(tmp_path/"coverage.sqlite3")
    store.set_setting("wallet_audit:last_scan",{
        "read_at":1_790_000_100.0,
        "coverage_complete":True,
        "chains":[
            {"chain":"ETHEREUM","transfers":11,"transactions":11,"full_transaction_history":True},
            {"chain":"POLYGON","transfers":4,"transactions":0,"full_transaction_history":True},
            {"chain":"BASE","transfers":0,"transactions":0,"full_transaction_history":True},
            {"chain":"ARBITRUM","transfers":0,"transactions":0,"full_transaction_history":False},
        ],
    })
    store.list_positions=lambda status=None:[{"chain":"ROBINHOOD_CHAIN","monitoring_class":"TACTICAL_CAMPAIGN"}]
    summary=wallet_audit_summary(_settings(),store)
    assert summary["metrics"]["active_chains"]==3
    assert summary["metrics"]["full_history_chains"]==2


def test_v095_wallet_audit_ui_is_separate_and_manual_review_is_explicit():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'data-section="walletaudit"' in html
    assert 'id="walletaudit-section"' in html
    assert "Investor Ledger" in html
    assert 'id="wallet-audit-scan-btn"' in html
    assert "Review queue" in html
    assert "Full on-chain activity" in html
    assert "Scan coverage & transaction costs" in html
    assert "function renderWalletAudit()" in js
    assert "New money / funding" in js
    assert "Transfer from/to one of my wallets or a bridge" in js
    assert "enter the real bank/card amount" in js
    assert "/api/wallet-audit/scan" in js
    assert "/api/wallet-audit/resolve" in js
    assert "$('[data-wallet-audit-review]').forEach" in js
    assert "$('[data-wallet-audit-activity]').forEach" in js
    assert "function showWalletAuditActivity" in js
    assert "Scanning wallet" in js


def test_deterministic_wallet_classifier_auto_tags_protocol_swaps_bridges_and_known_internal(tmp_path):
    store=Store(tmp_path/"auto.sqlite3")
    wallet=_settings_gbp().wallet_address
    internal="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    # Teach the classifier one human-confirmed internal wallet.
    historical=_event("old","0xold","IN",0.1)
    historical["from_address"]=internal
    historical["to_address"]=wallet
    store.upsert_wallet_audit_event(historical)
    store.resolve_wallet_audit_event("old",review_status="INTERNAL_TRANSFER",note="My other wallet")

    rows=[]
    for event_id,tx_hash,direction,asset,frm,to in [
        ("swap-in","0xswap","IN","DELTA","0xrouter",wallet),
        ("swap-out","0xswap","OUT","ETH",wallet,"0xrouter"),
        ("bridge","0xbridge","OUT","ETH",wallet,"0xbridgecontract"),
        ("lp","0xlp","OUT","DELTA",wallet,"0xpositionmanager"),
        ("internal","0xinternal","IN","ETH",internal,wallet),
        ("fund","0xfund","IN","ETH","0xprovider",wallet),
    ]:
        row=_event(event_id,tx_hash,direction,1.0)
        row["asset"]=asset
        row["from_address"]=frm
        row["to_address"]=to
        rows.append(row)

    txs=[
        {"tx_hash":"0xswap","kind":"SWAP"},
        {"tx_hash":"0xbridge","kind":"BRIDGE"},
        {"tx_hash":"0xlp","kind":"LP_ACTION"},
        {"tx_hash":"0xinternal","kind":"NATIVE_TRANSFER"},
        {"tx_hash":"0xfund","kind":"NATIVE_TRANSFER"},
    ]
    classified={x["id"]:x for x in _classify_with_transaction_context(store,rows,txs)}

    assert classified["swap-in"]["review_status"]=="INTERNAL_CONVERSION"
    assert classified["swap-out"]["review_status"]=="INTERNAL_CONVERSION"
    assert classified["bridge"]["review_status"]=="INTERNAL_TRANSFER"
    assert classified["lp"]["review_status"]=="INTERNAL_PROTOCOL"
    assert classified["internal"]["review_status"]=="INTERNAL_TRANSFER"

    # Direct inbound native value is not guessed as cash. It is surfaced to the user.
    assert classified["fund"]["review_status"]=="UNRESOLVED"
    automation=classified["fund"]["payload"]["automation"]
    assert automation["suggestion"]=="CONFIRMED_FUNDING"
    assert automation["confidence"]=="REVIEW"


def test_confirmed_gbp_funding_appends_clean_ledger_and_performance_cash_flow_once(tmp_path):
    store=Store(tmp_path/"rolling.sqlite3")
    store.set_setting("fx:USD:GBP",{"rate":0.8,"read_at":time.time(),"source":"TEST"})
    row=_event("fund","0xfund","IN",0.02)
    store.upsert_wallet_audit_event(row)

    resolve_wallet_audit_event(
        _settings_gbp(),store,"fund",
        classification="CONFIRMED_FUNDING",
        fiat_amount=50.0,
        note="Transak £50 including fees",
    )
    ledger=store.list_capital_ledger(20)
    assert len(ledger)==1
    assert ledger[0]["event_type"]=="EXTERNAL_FUNDING"
    assert ledger[0]["amount_gbp"]==50.0
    assert ledger[0]["asset"]=="ETH"
    assert ledger[0]["asset_amount"]==0.02
    assert ledger[0]["tx_hash"]=="0xfund"
    assert ledger[0]["source"]=="WALLET_AUDIT_CONFIRMED"

    flows=[
        x for x in store.list_financial_events(100)
        if x["event_type"]=="EXTERNAL_DEPOSIT"
    ]
    assert len(flows)==1
    assert abs(flows[0]["amount_usd"]-62.5)<1e-9
    assert flows[0]["payload"]["wallet_audit_event_id"]=="fund"

    # Re-saving the same review is idempotent for both ledgers.
    resolve_wallet_audit_event(
        _settings_gbp(),store,"fund",
        classification="CONFIRMED_FUNDING",
        fiat_amount=50.0,
        note="Transak £50 including fees",
    )
    assert len(store.list_capital_ledger(20))==1
    flows=[
        x for x in store.list_financial_events(100)
        if x["event_type"]=="EXTERNAL_DEPOSIT"
    ]
    assert len(flows)==1


def test_v095_final_ui_surfaces_review_queue_campaign_movements_and_automatic_scan():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert 'id="capital-ledger-review"' in html
    assert "Review queue" in html
    assert "Automatic reconciliation clear" in js
    assert "data-ledger-campaign-movements" in js
    assert "Confirm & update ledger" in js
    assert "automatic wallet reconciliation is ON" in js
    assert "15*60*1000" in js
    assert "scanWalletAudit(true)" in js
    assert "auto-classified" in js
