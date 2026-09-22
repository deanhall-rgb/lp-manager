from pathlib import Path
from lp_manager.db import Store


def test_position_snapshot_and_settings(tmp_path: Path):
    store=Store(tmp_path/"live.sqlite3")
    store.save_position_snapshot("p1", {"block_number":123,"live":True})
    assert store.get_position_snapshot("p1")["block_number"] == 123
    store.set_setting("live:last_refresh", {"ok":True})
    assert store.get_setting("live:last_refresh")["ok"] is True
