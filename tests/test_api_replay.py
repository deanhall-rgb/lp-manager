from pathlib import Path
from fastapi.testclient import TestClient
from lp_manager.api import create_app


def test_demo_replay_api_and_support_bundle(tmp_path: Path):
    c=TestClient(create_app(tmp_path))
    r=c.post('/api/replay/run',json={'scenario':'CORE_TREND'})
    assert r.status_code==200
    body=r.json()['result']
    assert body['no_lookahead'] is True
    assert body['summary']['strategy_reviews'] < body['summary']['replay_candles']/2
    runs=c.get('/api/replay/runs')
    assert runs.status_code==200 and len(runs.json())==1
    support=c.post('/api/support/bundle').json()
    assert c.get(support['download']).status_code==200


def test_risk_api_blocks_fragile_core(tmp_path: Path):
    c=TestClient(create_app(tmp_path))
    r=c.post('/api/risk/evaluate',json={'sleeve':'CORE_INCOME','candidate':{'chain_quality':90,'protocol_quality':90,'asset_conviction':90,'token_quality':90,'liquidity_stability':20,'fee_consistency':80,'pool_age_days':3,'tvl_usd':100000,'contract_risk':10,'exit_liquidity_score':20}})
    assert r.status_code==200
    assert not r.json()['eligible']
