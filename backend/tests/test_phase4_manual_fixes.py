import asyncio

import httpx


def _candidate(chunk_id, symbol, selected=True, score=1.0):
    return {
        "chunk_id": chunk_id,
        "symbol_name": symbol,
        "selected_file_match": selected,
        "final_score": score,
    }


def test_enumeration_reserves_distinct_selected_symbols_and_helpers():
    from app.services.project_service import _merge_selected_file_coverage

    endpoints = [_candidate(f"endpoint-{index}", f"method{index}", score=0.6) for index in range(3)]
    helper = _candidate("security-config", "configure", selected=False, score=2.0)
    result, removed = _merge_selected_file_coverage([helper, endpoints[0]], endpoints, 10, 3, 5, True)

    assert {item["chunk_id"] for item in result}.issuperset({"endpoint-0", "endpoint-1", "endpoint-2", "security-config"})
    assert removed >= 1


def test_selected_controller_retrieval_includes_three_endpoints_and_security_config(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "phase4-controller"
    with db() as connection:
        for file_id, path in (("controller-file", "src/ProductController.java"), ("config-file", "src/WebSecurityConfig.java")):
            connection.execute(
                """INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
                   VALUES (?, ?, ?, 'java', 100, 30, 1, 'now')""", (file_id, project_id, path),
            )
        chunks = [
            ("get", "controller-file", "getProducts", 10, "@GetMapping @PreAuthorize hasAuthority('STAFF')", "getmapping,preauthorize,hasauthority"),
            ("add", "controller-file", "addProduct", 15, "@PostMapping @PreAuthorize hasAuthority('MANAGER')", "postmapping,preauthorize,hasauthority"),
            ("remove", "controller-file", "removeProduct", 20, "@DeleteMapping @PreAuthorize hasAuthority('ADMIN')", "deletemapping,preauthorize,hasauthority"),
            ("config", "config-file", "configure", 5, "requestMatchers('/products').authenticated()", "permission_check,authentication"),
        ]
        for chunk_id, file_id, symbol, line, code, tags in chunks:
            connection.execute(
                """INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
                   VALUES (?, ?, ?, 'method', ?, 'C', ?, ?, ?, ?, ?, 'now')""",
                (chunk_id, project_id, file_id, symbol, line, line + 3, code, tags, f"code:{chunk_id}"),
            )
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    with db() as connection:
        package = project_service.retrieve_evidence_package(
            project_id, "identify every endpoint and access-control matrix", 10, connection,
            module_id="src/ProductController.java",
        )

    ids = {item["chunk_id"] for item in package["source_chunks"]}
    assert {"get", "add", "remove", "config"}.issubset(ids)
    assert package["diagnostics"]["selected_file_chunks_in_final"] == 3
    assert package["diagnostics"]["distinct_selected_file_symbols"] == 3
    assert package["diagnostics"]["enumeration_intent"] is True


def test_selected_file_diversification_and_cap():
    from app.services.project_service import _merge_selected_file_coverage

    selected = [
        _candidate("duplicate-a", "same", score=1.2),
        _candidate("duplicate-b", "same", score=1.1),
        *[_candidate(f"method-{index}", f"method{index}", score=1.0 - index / 20) for index in range(8)],
    ]
    helpers = [_candidate(f"helper-{index}", f"helper{index}", selected=False, score=0.5) for index in range(5)]
    result, _ = _merge_selected_file_coverage([*selected, *helpers], selected, 10, 3, 5, True)

    selected_result = [item for item in result if item["selected_file_match"]]
    assert len(selected_result) <= 5
    assert len({item["symbol_name"] for item in selected_result}) == len(selected_result)
    assert any(not item["selected_file_match"] for item in result)


def test_non_enumeration_only_reserves_one_selected_chunk():
    from app.services.project_service import _merge_selected_file_coverage

    selected = [_candidate(f"method-{index}", f"method{index}", score=0.2) for index in range(3)]
    helpers = [_candidate(f"helper-{index}", f"helper{index}", selected=False, score=1.0) for index in range(4)]
    result, _ = _merge_selected_file_coverage(helpers, selected, 4, 3, 5, False)

    assert sum(1 for item in result if item["selected_file_match"]) == 1


class _FakeResponse:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self._content}, "authorization": "Bearer secret-value"}


class _FakeClient:
    content = ""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse(self.content)


def _ollama_result(isolated_env, monkeypatch, content):
    from app.core.config import get_settings
    from app.services.llm import OllamaProvider

    _FakeClient.content = content
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    return asyncio.run(OllamaProvider(get_settings()).generate([{"role": "user", "content": "Question"}], "qwen"))


def test_ollama_plain_text_is_accepted_with_warning(isolated_env, monkeypatch):
    result = _ollama_result(isolated_env, monkeypatch, "A useful evidence-backed answer.")

    assert result["content"] == "A useful evidence-backed answer."
    assert result["validation_status"] == "completed_with_warnings"
    assert result["ok"] is True


def test_ollama_empty_and_think_only_are_empty_response(isolated_env, monkeypatch):
    empty = _ollama_result(isolated_env, monkeypatch, "")
    think_only = _ollama_result(isolated_env, monkeypatch, "<think>reasoning only</think>")

    assert empty["validation_status"] == "empty_response"
    assert think_only["validation_status"] == "empty_response"
    assert think_only["diagnostics"]["processing"]["think_tags_removed"] is True


def test_diagnostic_sanitization_removes_secrets_and_truncates():
    from app.services.llm import sanitize_diagnostic_text

    sanitized = sanitize_diagnostic_text("Authorization: Bearer abc123 API_KEY=topsecret " + "x" * 100, 70)

    assert "abc123" not in sanitized
    assert "topsecret" not in sanitized
    assert "[REDACTED]" in sanitized
    assert sanitized.endswith("… [truncated]")


def test_execution_details_never_adds_raw_cloud_response(isolated_env):
    from app.services.audit_service import _execution_details

    details = _execution_details(
        "id", "start", "ask", "completed", "q", "q", None, 10, [], [],
        "gemini", "model", 12, {"response_received": True}, None, {},
    )

    assert "sanitized_raw_response" not in details["provider"]
    assert details["processing"]["response_received"] is True


def test_execution_details_normalizes_legacy_timeout(isolated_env):
    from app.services.audit_service import _execution_details

    details = _execution_details(
        "id", "start", "compare", "timeout", "q", "q", None, 10, [], [],
        "ollama", "model", 60000, {}, None, {},
    )

    assert details["status"] == "provider_timeout"
