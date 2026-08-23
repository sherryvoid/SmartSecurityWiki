import asyncio
import json

import httpx


def test_evidence_role_detection_is_generic():
    from app.services.project_service import _extract_evidence_roles

    roles = _extract_evidence_roles("Identify every endpoint and which configured users can access each operation")
    assert {"needs_endpoint_declarations", "needs_route_resolution", "needs_authority_checks", "needs_user_assignments", "needs_security_configuration"}.issubset(roles)
    assert "needs_user_assignments" not in _extract_evidence_roles("Identify every endpoint and authority check")


def test_user_assignment_role_reserves_global_configuration_candidate():
    from app.services.project_service import _apply_evidence_role_coverage, _classify_evidence_roles

    endpoints = [{"chunk_id": f"e{i}", "selected_file_match": True, "http_method": "GET", "code_snippet": "@GetMapping @PreAuthorize", "file_path": "Controller.java", "symbol_name": f"m{i}", "final_score": 2 - i / 10} for i in range(3)]
    helpers = [{"chunk_id": f"h{i}", "selected_file_match": False, "code_snippet": "service helper", "file_path": "Helper.java", "symbol_name": f"h{i}", "final_score": 1 - i / 10} for i in range(2)]
    config = {"chunk_id": "users", "selected_file_match": False, "code_snippet": "new InMemoryUserDetailsManager(User.withUsername(name).authorities(values))", "file_path": "SecurityConfiguration.java", "symbol_name": "users", "final_score": 0.1}
    result = _apply_evidence_role_coverage([*endpoints, *helpers], [*endpoints, *helpers, config], {"needs_user_assignments", "needs_security_configuration"}, 5)

    assert {item["chunk_id"] for item in result}.issuperset({"e0", "e1", "e2", "users"})
    assert "needs_user_assignments" in _classify_evidence_roles(config)
    assert any(item["chunk_id"].startswith("h") for item in result)


def test_route_composition_variants():
    from app.services.project_service import _extract_java_class_route, _route_metadata

    assert _route_metadata("@GetMapping\nvoid all() {}", "java", None)["effective_route"] == "/"
    assert _route_metadata("@GetMapping\nvoid all() {}", "java", "/products")["effective_route"] == "/products"
    assert _route_metadata('@GetMapping("/{id}")\nvoid one() {}', "java", "/products")["effective_route"] == "/products/{id}"
    assert _route_metadata('@PostMapping(path = "/new")\nvoid add() {}', "java", "/products")["effective_route"] == "/products/new"
    assert _extract_java_class_route('@RequestMapping(value = "/products")\npublic class Products {}') == "/products"


class FakeResponse:
    def __init__(self, payload, status=200, text=""):
        self.payload = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://ollama/api/chat")
            raise httpx.HTTPStatusError("failed", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self):
        return self.payload


class FakeClient:
    responses = []
    payloads = []

    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, tb): return False
    async def post(self, url, json):
        self.payloads.append(json)
        return self.responses.pop(0)


def ollama_result(isolated_env, monkeypatch, response_payloads):
    from app.core.config import get_settings
    from app.services.llm import OllamaProvider

    FakeClient.responses = response_payloads
    FakeClient.payloads = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return asyncio.run(OllamaProvider(get_settings()).generate([{"role": "user", "content": "q"}], "qwen"))


def test_ollama_content_and_thinking_metadata_without_reasoning_leak(isolated_env, monkeypatch):
    result = ollama_result(isolated_env, monkeypatch, [FakeResponse({"message": {"content": "final", "thinking": "private reasoning"}, "done": True, "eval_count": 4})])
    serialized = json.dumps(result)

    assert result["content"] == "final"
    assert result["diagnostics"]["envelope"]["thinking_present"] is True
    assert result["diagnostics"]["envelope"]["thinking_length"] == len("private reasoning")
    assert "private reasoning" not in serialized
    assert FakeClient.payloads[0]["think"] is False


def test_ollama_thinking_only_and_fully_empty_are_distinct(isolated_env, monkeypatch):
    thinking = ollama_result(isolated_env, monkeypatch, [FakeResponse({"message": {"content": "", "thinking": "private"}, "done": True})])
    empty = ollama_result(isolated_env, monkeypatch, [FakeResponse({"message": {"content": ""}, "done": True})])

    assert thinking["diagnostics"]["processing"]["parse_status"] == "no_content_with_thinking"
    assert empty["diagnostics"]["processing"]["parse_status"] == "fully_empty_response"


def test_ollama_unsupported_think_option_retries_without_it(isolated_env, monkeypatch):
    result = ollama_result(isolated_env, monkeypatch, [
        FakeResponse({}, 400, "unknown field think"),
        FakeResponse({"message": {"content": "compatible answer"}, "done": True}),
    ])

    assert "think" in FakeClient.payloads[0]
    assert "think" not in FakeClient.payloads[1]
    assert result["diagnostics"]["envelope"]["think_option_fallback"] is True


def test_wiki_lifecycle_empty_parse_and_valid():
    from app.services.llm import generate_structured_security_wiki_diagnostic

    class Provider:
        def __init__(self, content, status=None): self.content, self.status = content, status
        async def generate(self, messages, model):
            return {"content": self.content, "ok": bool(self.content), "validation_status": self.status}

    empty = asyncio.run(generate_structured_security_wiki_diagnostic(Provider("", "empty_response"), [], "m"))
    malformed = asyncio.run(generate_structured_security_wiki_diagnostic(Provider("not json"), [], "m"))
    valid_payload = json.dumps({"module_overview": "ok", "entry_points": [], "access_control_matrix": [], "vertical_helpers": [], "requirement_traces": [], "limitations": "none"})
    valid = asyncio.run(generate_structured_security_wiki_diagnostic(Provider(valid_payload), [], "m"))

    assert empty[2] == "wiki_empty_response"
    assert malformed[2] == "wiki_parse_failed"
    assert valid[2] == "wiki_completed"


def test_empty_wiki_attempt_is_not_stored(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import WikiGenerateRequest
    from app.services import audit_service

    class EmptyProvider:
        name = "ollama"
        async def generate(self, messages, model):
            return {"content": "", "ok": False, "validation_status": "empty_response", "diagnostics": {"processing": {"response_received": True}}}

    init_db()
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (EmptyProvider(), "qwen"))
    monkeypatch.setattr(audit_service, "index_wiki_page", lambda *args, **kwargs: [])
    result = asyncio.run(audit_service.generate_wiki("project", WikiGenerateRequest(module_path="Controller.java", provider="ollama")))
    with db() as connection:
        stored_count = connection.execute("SELECT COUNT(*) AS count FROM wiki_pages").fetchone()["count"]

    assert result["validation_status"] == "wiki_empty_response"
    assert result["stored"] is False
    assert stored_count == 0


def test_valid_structured_wiki_is_stored(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import WikiGenerateRequest
    from app.services import audit_service

    payload = json.dumps({"module_overview": "ok", "entry_points": [], "access_control_matrix": [], "vertical_helpers": [], "requirement_traces": [], "limitations": "none"})

    class ValidProvider:
        name = "ollama"
        async def generate(self, messages, model): return {"content": payload, "ok": True, "diagnostics": {}}

    init_db()
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (ValidProvider(), "qwen"))
    monkeypatch.setattr(audit_service, "index_wiki_page", lambda *args, **kwargs: [])
    result = asyncio.run(audit_service.generate_wiki("project", WikiGenerateRequest(module_path="Controller.java", provider="ollama")))
    with db() as connection:
        stored_count = connection.execute("SELECT COUNT(*) AS count FROM wiki_pages").fetchone()["count"]

    assert result["validation_status"] == "wiki_completed"
    assert result["stored"] is True
    assert stored_count == 1
