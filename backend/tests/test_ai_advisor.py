import json

from app.core.config import settings
from app.services.ai.advisor import Advisor, Interpretation


def test_openai_adapter_requests_strict_schema_and_parses_typed_output(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "ai_api_key", "test-only")
    monkeypatch.setattr(settings, "ai_model", "gpt-4o-mini")
    captured = {}

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"output": [{"type": "message", "content": [{"type": "output_text",
                "text": json.dumps({"summary": "Evidence is incomplete.",
                    "uncertainty_flags": ["No verified transfer record"],
                    "next_step": "Retrieve current transfer evidence."})}]}]}

    class Client:
        def __init__(self, **kwargs): captured["timeout"] = kwargs["timeout"]
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, body=json)
            return Response()

    monkeypatch.setattr("app.services.ai.advisor.httpx.Client", Client)
    result = Advisor().interpret({"claim": "untrusted"})
    assert isinstance(result, Interpretation)
    assert result.next_step == "Retrieve current transfer evidence."
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["body"]["store"] is False
    assert captured["body"]["text"]["format"]["strict"] is True
    assert "Authorization" in captured["headers"]
