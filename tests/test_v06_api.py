from fastapi.testclient import TestClient
from lp_manager.api import create_app


def test_v06_product_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("LP_MANAGER_DEMO_SEED","true")
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    app=create_app(tmp_path)
    client=TestClient(app)
    health=client.get("/api/health").json()
    assert health["version"] == "0.8.4"
    auto=client.get("/api/automation").json()
    assert auto["policy"]["autonomous_signing"] is False
    brief=client.post("/api/ai/portfolio-brief").json()
    assert brief["ai_mode"].startswith("DETERMINISTIC_FALLBACK")
    positions=client.get("/api/positions").json()
    pid=positions[0]["id"]
    changed=client.post(f"/api/positions/{pid}/metadata",json={"display_name":"Test Campaign"}).json()
    assert changed["display_name"] == "Test Campaign"
