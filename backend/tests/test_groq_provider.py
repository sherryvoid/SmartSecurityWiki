import asyncio

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

    async def get(self, url, **kwargs):
        if self.capture is not None:
            self.capture.update(url=url, **kwargs)
        if self.error:
            raise self.error
        return self.response


def _response(status, payload):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://api.groq.com"))


def test_groq_defaults_and_missing_key_do_not_make_request(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import GroqProvider, provider_for

    settings = Settings(_env_file=None)
    provider, model = provider_for("groq", settings)
    assert isinstance(provider, GroqProvider)
    assert model == "openai/gpt-oss-20b"
    assert settings.groq_base_url == "https://api.groq.com/openai/v1"
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    result = asyncio.run(provider.generate([{"role": "user", "content": "question"}], model))
    assert result["ok"] is False
    assert result["raw"] == {"error": "missing_api_key"}


def test_groq_exact_request_reasoning_and_safe_response(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import GroqProvider

    capture = {}
    response = _response(200, {"id": "c1", "model": "openai/gpt-oss-20b", "choices": [{"message": {"content": "Grounded answer"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=response, capture=capture))
    settings = Settings(_env_file=None, groq_api_key="test-only-secret")
    result = asyncio.run(GroqProvider(settings).generate([{"role": "user", "content": "question"}], settings.groq_default_model))
    assert capture["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert capture["json"] == {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "question"}], "reasoning_effort": "medium", "include_reasoning": False, "max_completion_tokens": 2048}
    assert "temperature" not in capture["json"]
    assert result["content"] == "Grounded answer"
    assert result["raw"] == {"id": "c1", "model": "openai/gpt-oss-20b", "system_fingerprint": None}
    assert "test-only-secret" not in repr(result)


def test_groq_usage_normalization_and_versioned_cost(isolated_env):
    from app.db.database import init_db
    from app.services.usage_service import normalize_usage, persist_usage, usage_summary

    init_db()
    normalized = normalize_usage("groq", {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100, "completion_tokens_details": {"reasoning_tokens": 40}, "queue_time": .01, "prompt_time": .02, "completion_time": .03, "total_time": .06})
    assert normalized["provider_reported_reasoning_tokens"] == 40
    assert normalized["provider_queue_duration_ms"] == 10
    assert normalized["provider_total_duration_ms"] == 60
    assert normalize_usage("groq", {})["usage_source"] == "unavailable"
    persist_usage(execution_id="groq-exec", run_id="run", project_id="p", operation="ask", provider="groq", model="openai/gpt-oss-20b", normalized=normalized, duration_ms=70, composition={}, supplied_source=[], cited_source=[], wiki=[], source_hash="s", wiki_hash="w", status="completed")
    summary = usage_summary("p")
    row = summary["recent_executions"][0]
    assert row["api_cost"] == 0.000105
    assert row["pricing_revision"] == "groq-gpt-oss-20b-2025-08-05-v1"
    assert row["model_configuration"]["reasoning_effort"] == "medium"
    assert summary["by_model"][0]["provider"] == "groq"
    assert summary["by_model"][0]["reasoning_tokens"] == 40


def test_groq_health_discovery_filters_to_active_models(monkeypatch):
    from app.core.config import Settings
    from app.services import model_health

    missing = asyncio.run(model_health._groq_health(Settings(_env_file=None)))
    assert missing["reason"] == "GROQ_API_KEY not configured"
    response = _response(200, {"data": [{"id": "openai/gpt-oss-20b"}, {"id": "unapproved/model"}]})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=response))
    health = asyncio.run(model_health._groq_health(Settings(_env_file=None, groq_api_key="test-only-secret")))
    assert health["available"] is True
    assert health["available_models"] == ["openai/gpt-oss-20b"]
    assert "test-only-secret" not in repr(health)


def test_groq_safe_error_categories(monkeypatch):
    from app.core.config import Settings
    from app.services.llm import GroqProvider

    settings = Settings(_env_file=None, groq_api_key="test-only-secret")
    for status, category in ((401, "authentication"), (429, "rate_limit"), (404, "model_unavailable")):
        response = _response(status, {"error": {"message": "model unavailable"}})
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(response=response))
        result = asyncio.run(GroqProvider(settings).generate([], settings.groq_default_model))
        assert result["raw"]["category"] == category
        assert "test-only-secret" not in repr(result)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(error=httpx.ReadTimeout("slow")))
    timed_out = asyncio.run(GroqProvider(settings).generate([], settings.groq_default_model))
    assert timed_out["validation_status"] == "timeout"
    assert timed_out["content"] == "Groq did not complete within the configured timeout."


def test_compare_groq_gets_frozen_evidence_and_preserves_full_answer(isolated_env, monkeypatch):
    import json
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service

    init_db()
    evidence = [{"chunk_id": "E1", "file_path": "src/Auth.java", "symbol_name": "authorize", "start_line": 4, "end_line": 9, "language": "java", "code_snippet": "return permissionMapper.map(claims);"}]
    package = {"source_chunks": evidence, "wiki_chunks": [], "wiki_context": {"selected_wiki_chunks": []}, "diagnostics": {}}
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)
    received = {}

    class Provider:
        name = "groq"

        async def generate(self, messages, model):
            received["messages"], received["model"] = messages, model
            payload = {"answer": "Full Groq answer (E1).", "confidence": "high", "access_control_summary": "Preserved detail (E1).", "evidence_refs": ["E1"], "helper_chain": ["Mapper chain (E1)."], "limitations": [], "needs_review": False}
            return {"content": json.dumps(payload), "ok": True, "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), "openai/gpt-oss-20b"))
    result = asyncio.run(audit_service.compare_models("p", CompareRequest(question="Trace authorization", providers=["groq::openai/gpt-oss-20b", "gemini"])))
    groq = result["results"][0]
    serialized = repr(received["messages"])
    assert received["model"] == "openai/gpt-oss-20b"
    assert "return permissionMapper.map(claims);" in serialized
    assert groq["provider"] == "groq"
    assert "Full Groq answer (E1)." in groq["full_answer"]
    assert "Preserved detail (E1)." in groq["full_answer"]
    assert result["primary_evidence_match"] is True
    assert result["wiki_context_match"] is True
    assert result["rq2_comparison_eligible"] is True


def test_compare_keeps_failed_groq_result(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service

    init_db()
    evidence = [{"chunk_id": "E1", "file_path": "src/Auth.java", "symbol_name": "authorize", "start_line": 4, "end_line": 9, "language": "java", "code_snippet": "authorize();"}]
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *a, **k: {"source_chunks": evidence, "wiki_chunks": [], "wiki_context": {"selected_wiki_chunks": []}, "diagnostics": {}})
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)

    class Provider:
        name = "groq"
        async def generate(self, messages, model):
            return {"content": "", "ok": False, "error": {"user_message": "Groq rate limit reached.", "provider": "groq"}, "raw": {"error": "provider_error"}}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), "openai/gpt-oss-20b"))
    result = asyncio.run(audit_service.compare_models("p", CompareRequest(question="Trace authorization", providers=["groq", "gemini"])))
    assert len(result["results"]) == 2
    assert result["results"][0]["provider"] == "groq"
    assert result["results"][0]["validation_status"] == "provider_unavailable"
    assert result["rq2_comparison_eligible"] is False
