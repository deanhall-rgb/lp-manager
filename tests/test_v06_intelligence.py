from pathlib import Path
from lp_manager.config import Settings
from lp_manager.db import Store
from lp_manager.intelligence import IntelligenceService
from lp_manager.models import Position


def settings(tmp_path):
    return Settings(project_root=tmp_path,data_dir=tmp_path,database_path=tmp_path/"db.sqlite3",execution_mode="build_only",legacy_root=tmp_path,currency="GBP",demo_seed=False)


def test_ai_falls_back_without_key(tmp_path):
    store=Store(tmp_path/"db.sqlite3")
    store.upsert_position(Position(id="p",protocol="UNISWAP_V3",chain="BASE",pair="WETH/USDC",status="OPEN",lower_price=90,upper_price=120,current_price=100,capital_value=1000,current_value=1010,unclaimed_fees=5,fees_today=1,fees_7d=5,fees_30d=20,realised_fees=0,estimated_il=0,gas_costs=0,apr_current=10,apr_7d=10,opened_at=1,strategy_sleeve="CORE_INCOME"))
    svc=IntelligenceService(settings(tmp_path),store)
    out=svc.portfolio_brief()
    assert out["ai_mode"] == "DETERMINISTIC_FALLBACK"
    assert out["recommendation"]
    assert svc.status()["authority"] == "ADVISORY_ONLY"
