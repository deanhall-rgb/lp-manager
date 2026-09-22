from pathlib import Path
from lp_manager.db import Store
from lp_manager.replay import demo_replay_candles, run_replay


def test_replay_roundtrip_and_outcome_audit(tmp_path: Path):
    store=Store(tmp_path/'db.sqlite')
    result=run_replay(demo_replay_candles('CORE_TREND', points=120), sleeve='CORE_INCOME', warmup_candles=36)
    saved=store.save_replay_run(result, scenario='CORE_TREND')
    assert store.get_replay_run(saved['id'])['no_lookahead'] is True
    assert store.list_replay_runs()[0]['scenario']=='CORE_TREND'
    audit=store.record_outcome_audit(subject_type='TEST',subject_id='x',horizon='24_CANDLES',verdict='SUPPORTED',payload={'ok':True})
    assert store.list_outcome_audits()[0]['id']==audit['id']
