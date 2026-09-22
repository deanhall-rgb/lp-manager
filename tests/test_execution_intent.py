from pathlib import Path
from lp_manager.config import Settings
from lp_manager.db import Store
from lp_manager.execution import ExecutionService


def test_prepare_open_preserves_strategy_intent(tmp_path: Path):
    root=tmp_path
    settings=Settings(project_root=root, data_dir=root, legacy_root=root, database_path=root/'db.sqlite', execution_mode='build_only', currency='GBP', demo_seed=False)
    service=ExecutionService(settings, Store(settings.database_path))
    row=service.prepare_open({
        'pair':'WETH/USDC','chain':'BASE','lower_price':3000,'upper_price':5000,'capital_value':1000,
        'strategy_sleeve':'CORE_INCOME','directional_bias':'BULLISH',
        'inventory_intent':'ALLOW_ACCUMULATE_RISK_ASSET_ON_DOWNSIDE','target_hold_days':30,
    })
    assert row['intent']['chain']=='BASE'
    assert row['intent']['strategy_sleeve']=='CORE_INCOME'
    assert row['intent']['target_hold_days']==30
