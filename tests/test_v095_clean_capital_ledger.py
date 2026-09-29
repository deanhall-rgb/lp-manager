from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from lp_manager.capital_ledger import clean_capital_ledger, import_reviewed_baseline
from lp_manager.db import Store


def _settings():
    return SimpleNamespace(currency="GBP",wallet_address="0xabc")


def _audit_event(event_id,tx_hash,direction,asset,amount,frm,to):
    return {
        "id":event_id,"chain":"ROBINHOOD_CHAIN","tx_hash":tx_hash,"transfer_key":event_id,
        "occurred_at":1_790_000_000.0,"direction":direction,"category":"erc20" if asset!="ETH" else "external",
        "asset":asset,"token_address":"","amount":amount,"from_address":frm,"to_address":to,
        "source":"TEST","payload":{},
    }


def test_reviewed_baseline_produces_clean_lifetime_pnl_and_legacy_allocations(tmp_path):
    store=Store(tmp_path/"ledger.sqlite3")
    store.set_setting("fx:USD:GBP",{"rate":0.8,"read_at":time.time(),"source":"TEST"})
    store.save_wallet_snapshot({"total_tracked_value_usd":2097.55})
    payload={
        "version":"v0.9.5-reviewed-1","currency":"GBP","replace":True,
        "entries":[
            {"id":"a1","occurred_at":"2026-08-26T21:40:59+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":200.0,"asset":"ETH","asset_amount":0.10610123,"estimated_friction_gbp":4.41},
            {"id":"a2","occurred_at":"2026-08-30T23:50:23+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":120.0,"asset":"ETH","asset_amount":0.06274443,"estimated_friction_gbp":8.03},
            {"id":"a3","occurred_at":"2026-09-04T16:26:59+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":250.0,"asset":"ETH","asset_amount":0.13272617,"estimated_friction_gbp":8.94},
            {"id":"a4","occurred_at":"2026-09-08T00:30:47+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":500.0,"asset":"ETH","asset_amount":0.26236089,"estimated_friction_gbp":18.51},
            {"id":"legacy","occurred_at":"2026-09-23T08:39:06+01:00","event_type":"LEGACY_BROUGHT_FORWARD","amount_gbp":214.66,"metadata":{"allocations_gbp":{"ETH":80.0,"DELTA":67.33,"HOOKR":67.33}}},
            {"id":"a5","occurred_at":"2026-09-23T09:30:35+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":45.0,"asset":"ETH","asset_amount":0.02048696,"estimated_friction_gbp":3.46},
        ],
        "audit_rules":{},
    }
    result=import_reviewed_baseline(store,payload)
    assert result["ok"] is True

    ledger=clean_capital_ledger(_settings(),store)
    m=ledger["metrics"]
    assert m["cash_invested_gbp"]==1329.66
    assert m["cash_withdrawn_gbp"]==0.0
    assert m["current_portfolio_gbp"]==1678.04
    assert m["lifetime_pnl_gbp"]==348.38
    assert m["lifetime_return_pct"]==26.2
    assert m["estimated_onramp_friction_gbp"]==43.35
    legacy=next(x for x in ledger["entries"] if x["id"]=="legacy")
    assert legacy["display_label"]=="MoonPay funding"
    assert legacy["display_note"]==""
    assert legacy["display_asset"]=="ETH"
    assert legacy["display_asset_amount"]>0
    assert legacy["display_reference"]=="Manual"


def test_reviewed_baseline_bulk_reconciles_funding_internal_protocol_bridge_and_noise(tmp_path):
    store=Store(tmp_path/"reconcile.sqlite3")
    wallet="0x1111111111111111111111111111111111111111"
    funding="0xfund"
    legacy="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    bridge="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    protocol="0xcccccccccccccccccccccccccccccccccccccccc"

    store.upsert_wallet_audit_event(_audit_event("fund",funding,"IN","ETH",0.1,"0xprovider",wallet))
    store.upsert_wallet_audit_event(_audit_event("legacy","0xlegacy","IN","DELTA",1000,legacy,wallet))
    store.upsert_wallet_audit_event(_audit_event("bridge","0xbridge","OUT","ETH",0.1,wallet,bridge))
    store.upsert_wallet_audit_event(_audit_event("spam","0xspam","IN","Play",777,"0xspammy",wallet))
    store.upsert_wallet_audit_event(_audit_event("protocol","0xproto","IN","DELTA",50,protocol,wallet))

    store.upsert_wallet_audit_transaction({
        "id":"wat:proto","chain":"ROBINHOOD_CHAIN","tx_hash":"0xproto","occurred_at":1_790_000_001,
        "direction":"OUT","kind":"CONTRACT_CALL","method":"","from_address":wallet,"to_address":protocol,
        "native_symbol":"ETH","value_native":0,"gas_native":0.00001,"success":True,"source":"TEST","payload":{},
    })

    payload={
        "currency":"GBP","entries":[{"id":"cash","occurred_at":1_790_000_000,"event_type":"EXTERNAL_FUNDING","amount_gbp":100}],
        "audit_rules":{
            "funding_transactions":{funding:{"amount_gbp":100,"note":"Reviewed funding"}},
            "internal_addresses":[legacy,bridge],
            "ignore_assets":["Play"],
        },
    }
    result=import_reviewed_baseline(store,payload)
    assert result["meta"]["reconciliation"]["funding"]==1
    assert result["meta"]["reconciliation"]["internal_transfer"]==2
    assert result["meta"]["reconciliation"]["internal_protocol"]==1
    assert result["meta"]["reconciliation"]["ignored"]==1
    rows={x["id"]:x for x in store.list_wallet_audit_events(20)}
    assert rows["fund"]["review_status"]=="CONFIRMED_FUNDING"
    assert rows["legacy"]["review_status"]=="INTERNAL_TRANSFER"
    assert rows["bridge"]["review_status"]=="INTERNAL_TRANSFER"
    assert rows["protocol"]["review_status"]=="INTERNAL_PROTOCOL"
    assert rows["spam"]["review_status"]=="IGNORE"


def test_clean_ledger_ui_hides_raw_audit_behind_advanced_details():
    root=Path(__file__).parents[1]
    html=(root/"lp_manager"/"static"/"index.html").read_text(encoding="utf-8")
    js=(root/"lp_manager"/"static"/"app.js").read_text(encoding="utf-8")

    assert "Investor Ledger" in html
    assert 'id="capital-ledger-metrics"' in html
    assert 'id="capital-ledger-journal"' in html
    assert 'id="capital-ledger-campaigns"' in html
    assert "Advanced wallet audit evidence" in html
    assert "function renderCapitalLedger()" in js
    assert "/api/capital-ledger/import" in js
    assert "renderCapitalLedger();renderWalletAudit()" in js
    assert "Capital invested" in js
    assert "Exposure P/L" in js
    assert "Reviewed legacy basis" not in js


def test_campaign_capital_uses_open_position_basis_plus_traced_wallet_basis(tmp_path, monkeypatch):
    import lp_manager.capital_ledger as capital_ledger

    store=Store(tmp_path/"campaign_basis.sqlite3")
    store.set_setting("fx:USD:GBP",{"rate":0.8,"read_at":time.time(),"source":"TEST"})
    store.save_wallet_snapshot({"total_tracked_value_usd":500.0})

    payload={
        "currency":"GBP",
        "entries":[
            {"id":"cash","occurred_at":"2026-09-01T10:00:00+01:00","event_type":"EXTERNAL_FUNDING","amount_gbp":200.0,"asset":"ETH","asset_amount":0.1},
        ],
        "audit_rules":{},
    }
    import_reviewed_baseline(store,payload)

    wallet="0xabc"
    store.upsert_wallet_audit_event({
        "id":"swap-in","chain":"ROBINHOOD_CHAIN","tx_hash":"0xswap","transfer_key":"1",
        "occurred_at":1_788_000_000.0,"direction":"OUT","category":"external","asset":"ETH",
        "token_address":"","amount":0.02,"from_address":wallet,"to_address":"0xrouter",
        "source":"TEST","payload":{},"review_status":"INTERNAL_CONVERSION",
    })
    store.upsert_wallet_audit_event({
        "id":"swap-out","chain":"ROBINHOOD_CHAIN","tx_hash":"0xswap","transfer_key":"2",
        "occurred_at":1_788_000_000.0,"direction":"IN","category":"erc20","asset":"DELTA",
        "token_address":"0xdelta","amount":1000.0,"from_address":"0xrouter","to_address":wallet,
        "source":"TEST","payload":{},"review_status":"INTERNAL_CONVERSION",
    })
    store.upsert_wallet_audit_transaction({
        "id":"wat:swap","chain":"ROBINHOOD_CHAIN","tx_hash":"0xswap","occurred_at":1_788_000_000.0,
        "direction":"OUT","kind":"SWAP","method":"swap","from_address":wallet,"to_address":"0xrouter",
        "native_symbol":"ETH","value_native":0.02,"gas_native":0.0,"success":True,"source":"TEST","payload":{},
    })

    monkeypatch.setattr(capital_ledger,"build_campaigns",lambda store:[{
        "id":"campaign:ROBINHOOD_CHAIN:DELTA","asset_symbol":"DELTA","label":"DELTA","status":"ACTIVE",
        "marked_exposure_usd":250.0,"known_campaign_pnl_usd":10.0,"lifetime_fees_usd":12.0,
        "transaction_costs_usd":1.0,
        "wallet_inventory":{"balance":500.0,"value_usd":100.0},
        "positions":[{"status":"OPEN","opening_capital_usd":100.0}],
        "open_positions":1,"closed_positions":0,
    }])

    ledger=clean_capital_ledger(_settings(),store)
    row=ledger["campaigns"][0]
    # ETH cash basis is £2,000/ETH, so 0.02 ETH -> £40 of DELTA acquisition basis.
    # Half the acquired DELTA remains in the wallet -> £20 wallet basis.
    # Open LP basis is $100 * 0.8 = £80.
    assert row["wallet_inventory_basis_gbp"]==20.0
    assert row["open_position_basis_gbp"]==80.0
    assert row["capital_invested_gbp"]==100.0
    assert row["marked_exposure_gbp"]==200.0
    assert row["exposure_pnl_gbp"]==100.0
