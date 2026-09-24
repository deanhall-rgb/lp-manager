import sqlite3
from lp_manager.db import Store


def test_v05_database_migrates_product_columns(tmp_path):
    path=tmp_path/"old.sqlite3"
    con=sqlite3.connect(path)
    con.execute("CREATE TABLE positions (id TEXT PRIMARY KEY, protocol TEXT NOT NULL, chain TEXT NOT NULL, pair TEXT NOT NULL, status TEXT NOT NULL, lower_price REAL NOT NULL, upper_price REAL NOT NULL, current_price REAL NOT NULL, capital_value REAL NOT NULL, current_value REAL NOT NULL, unclaimed_fees REAL NOT NULL DEFAULT 0, fees_today REAL NOT NULL DEFAULT 0, fees_7d REAL NOT NULL DEFAULT 0, fees_30d REAL NOT NULL DEFAULT 0, realised_fees REAL NOT NULL DEFAULT 0, estimated_il REAL NOT NULL DEFAULT 0, gas_costs REAL NOT NULL DEFAULT 0, apr_current REAL NOT NULL DEFAULT 0, apr_7d REAL NOT NULL DEFAULT 0, opened_at REAL NOT NULL, token_id TEXT, campaign_id TEXT, source TEXT NOT NULL DEFAULT 'manual', notes TEXT NOT NULL DEFAULT '', strategy_sleeve TEXT NOT NULL DEFAULT 'TACTICAL_CAMPAIGN', directional_bias TEXT NOT NULL DEFAULT 'NEUTRAL', inventory_intent TEXT NOT NULL DEFAULT 'BALANCED', target_hold_days REAL NOT NULL DEFAULT 3.0, monitoring_class TEXT NOT NULL DEFAULT 'ACTIVE')")
    con.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, position_id TEXT, created_at REAL NOT NULL, severity TEXT NOT NULL, action TEXT NOT NULL, confidence REAL NOT NULL, summary TEXT NOT NULL, rationale TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN')")
    con.commit(); con.close()
    Store(path)
    con=sqlite3.connect(path)
    pos={r[1] for r in con.execute('PRAGMA table_info(positions)')}
    dec={r[1] for r in con.execute('PRAGMA table_info(decisions)')}
    con.close()
    assert {"display_name","entry_thesis","exit_goal","cost_basis_quality","strategy_version"} <= pos
    assert {"source","evidence_json"} <= dec
