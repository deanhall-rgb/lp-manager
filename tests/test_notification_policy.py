from lp_manager.notification_policy import evaluate_notification


def test_action_notifies():
    r = evaluate_notification({"position_id":"p1","severity":"ACTION","code":"EDGE","material":True}, now=1000)
    assert r["send"]


def test_watch_is_deduplicated():
    r = evaluate_notification({"position_id":"p1","severity":"WATCH","code":"EDGE","material":True}, last_sent_at=950, now=1000)
    assert not r["send"]
    assert "DEDUP_COOLDOWN_ACTIVE" in r["reasons"]
