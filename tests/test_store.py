from pathlib import Path
import sqlite3

from lp_manager.db import Store
from lp_manager.models import Position


def make_position(**overrides):
    base = dict(
        id="p1", protocol="UNISWAP_V3", chain="BASE", pair="A/B", status="OPEN",
        lower_price=1, upper_price=2, current_price=1.5, capital_value=100, current_value=105,
        unclaimed_fees=1, fees_today=0.2, fees_7d=1, fees_30d=2, realised_fees=0,
        estimated_il=-0.5, gas_costs=0.1, apr_current=30, apr_7d=25, opened_at=1,
    )
    base.update(overrides)
    return Position(**base)


def test_store_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite")
    store.upsert_position(make_position(strategy_sleeve="CORE_INCOME", monitoring_class="LOW_TOUCH"))
    row = store.get_position("p1")
    assert row and row["pair"] == "A/B"
    assert row["strategy_sleeve"] == "CORE_INCOME"


def test_store_migrates_v01_database(tmp_path: Path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE positions (id TEXT PRIMARY KEY, protocol TEXT NOT NULL, chain TEXT NOT NULL, pair TEXT NOT NULL, status TEXT NOT NULL, lower_price REAL NOT NULL, upper_price REAL NOT NULL, current_price REAL NOT NULL, capital_value REAL NOT NULL, current_value REAL NOT NULL, unclaimed_fees REAL NOT NULL DEFAULT 0, fees_today REAL NOT NULL DEFAULT 0, fees_7d REAL NOT NULL DEFAULT 0, fees_30d REAL NOT NULL DEFAULT 0, realised_fees REAL NOT NULL DEFAULT 0, estimated_il REAL NOT NULL DEFAULT 0, gas_costs REAL NOT NULL DEFAULT 0, apr_current REAL NOT NULL DEFAULT 0, apr_7d REAL NOT NULL DEFAULT 0, opened_at REAL NOT NULL, token_id TEXT, campaign_id TEXT, source TEXT NOT NULL DEFAULT 'manual', notes TEXT NOT NULL DEFAULT '')")
    con.commit(); con.close()
    store = Store(path)
    store.upsert_position(make_position())
    row = store.get_position("p1")
    assert row["strategy_sleeve"] == "TACTICAL_CAMPAIGN"
    assert row["monitoring_class"] == "ACTIVE"
