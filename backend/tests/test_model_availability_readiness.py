import asyncio

import httpx

from app.core.config import Settings
from app.services import llm, model_health


def test_missing_cloud_keys_are_unavailable_without_leaking_values():
    settings = Settings(
        openai_api_key="",
        gemini_api_key="",
        groq_api_key="",
        openrouter_api_key="",
        embedding_provider="hash",
    )
    health = asyncio.run(model_health.models_health(settings))
    for provider in ("openai", "gemini", "groq", "openrouter"):
        assert health[provider]["available"] is False
        assert health[provider]["status"] == "Not configured"
    serialized = str(health).lower()
    assert "authorization" not in serialized
    assert "bearer " not in serialized


def test_ollama_unreachable_is_nonfatal(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            request = httpx.Request("GET", "http://localhost:11434/api/tags")
            raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(model_health.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(model_health._ollama_health(Settings(embedding_provider="hash")))
    assert result["reachable"] is False
    assert result["available"] is False
    assert result["status"] == "Ollama not running"


def test_reachable_ollama_missing_configured_model_is_unavailable(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "another-model:latest"}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

    monkeypatch.setattr(model_health.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(model_health._ollama_health(Settings(ollama_default_model="required:1", embedding_provider="hash")))
    assert result["reachable"] is True
    assert result["default_model_exists"] is False
    assert result["available"] is False
    assert result["status"] == "Model not found"


def test_ollama_availability_requires_exact_selected_model(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"models": [{"name": "installed:1"}]}

    monkeypatch.setattr(llm.httpx, "get", lambda *_args, **_kwargs: Response())
    assert llm.is_provider_available("ollama", "installed:1") is True
    assert llm.is_provider_available("ollama", "missing:1") is False


def test_missing_key_response_preserves_provider_identity():
    result = llm.missing_key_response("Gemini")
    assert result["ok"] is False
    assert result["validation_status"] == "provider_unavailable"
    assert result["error"]["provider"] == "gemini"
    assert result["error"]["error_code"] == "MISSING_API_KEY"


def test_cloud_failures_are_structured_and_do_not_expose_request_data():
    for status, code in ((401, "AUTHENTICATION"), (404, "MODEL_UNAVAILABLE"), (429, "RATE_LIMIT"), (503, "PROVIDER")):
        result = llm.cloud_http_error_response("Gemini", status)
        assert result["validation_status"] == "provider_unavailable"
        assert result["error"]["provider"] == "gemini"
        assert code in result["error"]["error_code"]
        assert "api key" not in str(result).lower()
