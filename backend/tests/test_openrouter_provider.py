import asyncio
from pathlib import Path

import httpx


class _Client:
    def __init__(self, response=None, error=None, capture=None, **kwargs):
        self.response, self.error, self.capture = response, error, capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        if self.capture is not None:
            self.capture.update(url=url, **kwargs)
        if self.error:
            raise self.error
        return self.response


def _response(status, payload, headers=None):
    return httpx.Response(status, json=payload, headers=headers, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))


def test_openrouter_config_registry_and_missing_key(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import OpenRouterProvider, active_provider_names, provider_for

    settings = Settings(_env_file=None)
    provider, model = provider_for("openrouter", settings)
    assert isinstance(provider, OpenRouterProvider)
    assert model == "openai/gpt-4o-mini"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert "openrouter" in active_provider_names()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    result = asyncio.run(provider.generate([], model))
    assert result["raw"] == {"error": "missing_api_key"}


def test_openrouter_uses_configured_url_model_and_unchanged_messages(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import OpenRouterProvider

    capture = {}
    response = _response(200, {"id": "run-1", "model": "openai/gpt-4o-mini", "provider": "OpenAI", "choices": [{"message": {"content": "Grounded answer"}}], "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=response, capture=capture))
    settings = Settings(_env_file=None, openrouter_api_key="test-only-secret", openrouter_base_url="https://router.test/v1", openrouter_model="openai/gpt-4o-mini")
    messages = [{"role": "system", "content": "frozen contract"}, {"role": "user", "content": "[E1] policy evidence"}]
    result = asyncio.run(OpenRouterProvider(settings).generate(messages, settings.openrouter_model))
    assert capture["url"] == "https://router.test/v1/chat/completions"
    assert capture["json"] == {"model": "openai/gpt-4o-mini", "messages": messages, "temperature": 0.1, "max_tokens": 2048}
    assert result["content"] == "Grounded answer"
    assert result["usage"]["total_tokens"] == 25
    assert "test-only-secret" not in repr(result)


def test_openrouter_usage_and_research_metadata(isolated_env):
    from app.db.database import init_db
    from app.services.usage_service import normalize_usage, persist_usage, usage_summary

    init_db()
    normalized = normalize_usage("openrouter", {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "cost": 0.00125})
    assert (normalized["provider_reported_input_tokens"], normalized["provider_reported_output_tokens"], normalized["provider_reported_total_tokens"]) == (20, 5, 25)
    persist_usage(execution_id="or-exec", run_id="run", project_id="p", operation="ask", provider="openrouter", model="openai/gpt-4o-mini", normalized=normalized, duration_ms=12, composition={"supplied_source_blocks": 1}, supplied_source=[{"chunk_id": "c1"}], cited_source=[], wiki=[], source_hash="hash", wiki_hash="wiki", status="completed")
    row = usage_summary("p")["recent_executions"][0]
    assert row["provider"] == "openrouter"
    assert row["model"] == "openai/gpt-4o-mini"
    assert row["api_cost"] == 0.00125
    assert row["pricing_revision"] == "openrouter-provider-reported"
    assert row["model_configuration"]["deployment_route"] == "openai/gpt-4o-mini accessed through OpenRouter"
    assert row["model_configuration"]["serialization_version"] == "compact-evidence-v1"


def test_openrouter_usage_summary_recovers_reported_cost_from_older_rows(isolated_env):
    from app.db.database import db, init_db
    from app.services.usage_service import normalize_usage, persist_usage, usage_summary

    init_db()
    normalized = normalize_usage("openrouter", {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40, "cost": 0.0025})
    persist_usage(execution_id="legacy-cost", run_id="run", project_id="p", operation="ask", provider="openrouter", model="openai/gpt-5.1", normalized=normalized, duration_ms=10, composition={}, supplied_source=[], cited_source=[], wiki=[], source_hash="s", wiki_hash="w", status="completed")
    with db() as connection:
        connection.execute("UPDATE model_usage SET api_cost=NULL, pricing_revision=NULL WHERE execution_id='legacy-cost'")
    row = usage_summary("p")["recent_executions"][0]
    assert row["api_cost"] == 0.0025
    assert row["pricing_revision"] == "openrouter-provider-reported"


def test_openrouter_malformed_auth_rate_limit_server_and_timeout_are_safe(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import OpenRouterProvider

    secret = "test-only-secret"
    settings = Settings(_env_file=None, openrouter_api_key=secret)
    malformed = _response(200, {"choices": []})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=malformed))
    assert asyncio.run(OpenRouterProvider(settings).generate([], settings.openrouter_model))["error"]["error_code"] == "OPENROUTER_MALFORMED"
    for status, category in ((401, "AUTHENTICATION"), (403, "AUTHENTICATION"), (429, "RATE_LIMIT"), (503, "PROVIDER")):
        response = _response(status, {"error": {"type": "safe-type", "code": "safe-code", "message": "safe message"}}, {"x-request-id": "req-safe", "retry-after": "5"})
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=response))
        result = asyncio.run(OpenRouterProvider(settings).generate([], settings.openrouter_model))
        assert result["error"]["error_code"] == f"OPENROUTER_{category}"
        assert result["raw"]["request_id"] == "req-safe"
        assert secret not in repr(result)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(error=httpx.ReadTimeout("slow")))
    timeout = asyncio.run(OpenRouterProvider(settings).generate([], settings.openrouter_model))
    assert timeout["validation_status"] == "timeout"
    assert secret not in repr(timeout)


def test_openrouter_health_and_frontend_label_do_not_expose_configuration():
    from app.core.config import Settings
    from app.services.model_health import models_health

    health = asyncio.run(models_health(Settings(_env_file=None, openrouter_api_key="test-only-secret")))
    assert health["openrouter"]["available"] is True
    assert "test-only-secret" not in repr(health)
    frontend = (Path(__file__).parents[2] / "frontend/src/pages/ProjectWorkspace.tsx").read_text(encoding="utf-8")
    assert 'if (provider === "openrouter") return "GPT-4o Mini"' not in frontend
    assert "health?.default_model" in frontend
    assert "OPENROUTER_API_KEY" not in frontend
    assert "https://openrouter.ai" not in frontend
