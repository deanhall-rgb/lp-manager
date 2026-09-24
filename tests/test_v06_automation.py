from lp_manager.automation_policy import get_policy, update_policy
from lp_manager.db import Store


def test_automation_hard_blocks_cannot_be_enabled(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    out = update_policy(store, {"autonomous_signing": True, "autonomous_broadcast": True, "ai_reviews": False})
    assert out["policy"]["autonomous_signing"] is False
    assert out["policy"]["autonomous_broadcast"] is False
    assert out["policy"]["ai_reviews"] is False
    assert get_policy(store)["authority"] == "BUILD_ONLY_MANUAL_APPROVAL"
