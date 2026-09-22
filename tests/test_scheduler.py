import pytest
from lp_manager.scheduler import assert_single_owner, due_jobs, scheduler_manifest


def test_manifest_has_single_owner_per_job():
    assert_single_owner()
    keys=[r["key"] for r in scheduler_manifest()]
    assert len(keys)==len(set(keys))


def test_event_jobs_are_not_polled():
    keys={r["key"] for r in due_jobs({},now=1000)}
    assert "strategy_review" not in keys
    assert "ai_review" not in keys
    assert "market_refresh" in keys


def test_duplicate_job_is_rejected():
    with pytest.raises(RuntimeError):
        assert_single_owner([{"key":"x","owner":"a"},{"key":"x","owner":"b"}])
