import json
from lp_manager.config import Settings
from lp_manager.db import Store
from lp_manager.intelligence import IntelligenceService


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        payload = {
            "headline": "Hold",
            "status": "GREEN",
            "confidence": 84,
            "recommendation": "Keep the current range",
            "reasons": ["Range remains healthy"],
            "risks": ["Market regime can change"],
            "counterargument": "A tighter range could earn more",
            "invalidation": "Material range exit",
            "next_review": "Routine cadence",
        }
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}]}


def _settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        data_dir=tmp_path,
        database_path=tmp_path / "db.sqlite3",
        execution_mode="build_only",
        legacy_root=tmp_path,
        currency="GBP",
        demo_seed=False,
        openai_api_key="test-key",
        openai_model="gpt-5.6-terra",
    )


def test_openai_advisory_uses_strict_structured_output(tmp_path, monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("lp_manager.intelligence.requests.post", fake_post)
    service = IntelligenceService(_settings(tmp_path), Store(tmp_path / "db.sqlite3"))
    result = service._ask("Review", {"x": 1}, {"headline": "fallback"})

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["json"]["model"] == "gpt-5.6-terra"
    fmt = captured["json"]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert result["ai_mode"] == "OPENAI_RESPONSES"
    assert result["confidence"] == 84
