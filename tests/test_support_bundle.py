import json
import zipfile
from pathlib import Path
from lp_manager.db import Store
from lp_manager.support_bundle import build_support_bundle


def test_support_bundle_redacts_secrets(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite")
    path = build_support_bundle(store, output_dir=tmp_path, extra={"rpc_url":"https://secret", "ok":True})
    with zipfile.ZipFile(path) as zf:
        extra = json.loads(zf.read("extra.json"))
    assert extra["rpc_url"] == "<redacted>"
