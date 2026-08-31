import asyncio
import json


def _chunk(chunk_id, code="code", **extra):
    return {"chunk_id": chunk_id, "file_path": extra.pop("file_path", f"src/{chunk_id}.java"), "symbol_name": extra.pop("symbol_name", chunk_id), "start_line": 1, "end_line": 3, "language": "java", "code_snippet": code, **extra}


def test_shared_hash_tracks_order_and_content():
    from app.services.audit_service import _freeze_shared_evidence_package
    first = _freeze_shared_evidence_package([_chunk("a"), _chunk("b")])
    same = _freeze_shared_evidence_package([_chunk("a"), _chunk("b")])
    reordered = _freeze_shared_evidence_package([_chunk("b"), _chunk("a")])
    changed = _freeze_shared_evidence_package([_chunk("a", "changed"), _chunk("b")])
    assert first == same
    assert first["shared_evidence_hash"] != reordered["shared_evidence_hash"]
    assert first["shared_evidence_hash"] != changed["shared_evidence_hash"]


def test_user_assignment_requires_principal_and_authority_content():
    from app.services.project_service import _classify_evidence_roles
    generic = _chunk("generic", "class WebSecurityConfig { UserDetailsService users() {} }")
    assigned = _chunk("assigned", 'User.withUsername("demo").authorities("READ").build()')
    assert "needs_user_assignments" not in _classify_evidence_roles(generic)
    assert "needs_user_assignments" in _classify_evidence_roles(assigned)


def test_route_state_semantics():
    from app.services.project_service import _route_metadata
    root = _route_metadata("@GetMapping\nvoid all() {}", "java", None)
    assert root == {"class_route": None, "method_route": "", "effective_route": "/", "http_method": "GET", "class_route_state": "absent", "method_route_state": "explicit_empty", "resolution_status": "resolved"}
    unavailable = _route_metadata("def handler(): pass", "python", None)
    assert unavailable["class_route_state"] == "unavailable"
    assert unavailable["resolution_status"] == "unresolved"


def test_valid_cloud_parse_forces_consistent_processing_state():
    from app.services.audit_service import _execution_details
    details = _execution_details("id", "start", "ask", "valid_json", "q", "q", None, 1, [_chunk("a")], [], "gemini", "model", 10, {})
    assert details["processing"]["response_received"] is True
    assert details["processing"]["content_present"] is True
    assert details["processing"]["parse_status"] == "valid_json"
    assert details["processing"]["schema_validation_status"] == "valid"


def test_serialized_diagnostics_are_exact_and_safe():
    from app.services.audit_service import _execution_details
    chunks = [_chunk("a"), _chunk("b")]
    details = _execution_details("id", "start", "compare", "valid_simple", "q", "q", None, 2, chunks, [], "ollama", "qwen", 10, {}, retrieval={"serialized_chunk_ids": ["a", "b"], "retrieved_chunk_ids": ["a", "b"], "evidence_package_match": True})
    supplied = details["provider"]["evidence_supplied_to_model"]
    assert [item["chunk_id"] for item in supplied] == ["a", "b"]
    assert all(item["serialized_character_count"] > 0 for item in supplied)
    assert all("code_snippet" not in item for item in supplied)


def test_compare_sends_identical_ordered_evidence_to_cloud_and_ollama(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    chunks = [_chunk(str(index)) for index in range(1, 11)]
    package = {"source_chunks": chunks, "wiki_chunks": [], "diagnostics": {"expanded_query": "q"}}
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: package)
    prompts = {}

    class Provider:
        def __init__(self, name): self.name = name
        async def generate(self, messages, model):
            prompts[self.name] = messages
            if self.name == "ollama":
                return {"content": "answer", "ok": True, "validation_status": "valid_simple", "diagnostics": {"processing": {"response_received": True, "content_present": True, "content_length": 6}}}
            payload = {"answer": "answer", "confidence": "high", "evidence_refs": ["1"], "helper_chain": [], "limitations": [], "needs_review": False}
            return {"content": json.dumps(payload), "ok": True, "diagnostics": {"processing": {"response_received": True, "content_present": True}}}

    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(name), "model"))
    result = asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=["gemini", "ollama"])))
    expected = [str(index) for index in range(1, 11)]
    assert result["comparison_valid"] is True
    assert [item["serialized_chunk_ids"] for item in result["results"]] == [expected, expected]
    assert len({item["shared_evidence_hash"] for item in result["results"]}) == 1
    assert all("[E10]" in messages[-1]["content"] for messages in prompts.values())


def test_compare_is_invalid_when_one_identical_package_fails_context_preflight(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    chunks = [_chunk(str(index), "x" * 40) for index in range(1, 11)]
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: {"source_chunks": chunks, "wiki_chunks": [], "diagnostics": {}})
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)

    class Provider:
        def __init__(self, name):
            self.name = name
            self.calls = 0
            if name == "ollama":
                from app.core.config import Settings
                self.settings = Settings(ollama_context_length=100, ollama_num_predict=20)

        def count_prompt_tokens(self, messages, model):
            return 90

        async def generate(self, messages, model):
            self.calls += 1
            if self.name == "ollama":
                return {"content": "answer", "ok": True, "validation_status": "valid_simple", "diagnostics": {}}
            return {"content": json.dumps({"answer": "answer", "confidence": "high", "evidence_refs": [], "helper_chain": [], "limitations": [], "needs_review": False}), "ok": True, "diagnostics": {}}

    providers = {}
    def provider_for(name):
        providers[name] = Provider(name)
        return providers[name], "neutral-model"
    monkeypatch.setattr(audit_service, "provider_for", provider_for)
    result = asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=["gemini", "ollama"])))
    assert result["primary_evidence_match"] is True
    assert result["results"][1]["evidence_package_match"] is True
    assert result["results"][1]["validation_status"] == "provider_context_incompatible"
    assert providers["ollama"].calls == 0
    assert result["effective_context_valid"] is False
    assert result["comparison_valid"] is False


def test_compare_remains_valid_when_both_prompt_preflights_pass(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    chunks = [_chunk("one")]
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: {"source_chunks": chunks, "wiki_chunks": [], "diagnostics": {}})
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)

    class Provider:
        name = "ollama"
        def __init__(self):
            from app.core.config import Settings
            self.settings = Settings(ollama_context_length=100, ollama_num_predict=20)
        def count_prompt_tokens(self, messages, model): return 70
        async def generate(self, messages, model): return {"content": "answer", "ok": True, "validation_status": "valid_simple", "diagnostics": {}}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), f"{name}-model"))
    result = asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=["ollama::model-a", "ollama::model-b"])))
    assert all(item["effective_context_valid"] for item in result["results"])
    assert result["effective_context_valid"] is True
    assert result["comparison_valid"] is True


def test_evaluation_mismatch_cannot_be_scored(isolated_env):
    from app.db.database import db, init_db
    from app.db.schemas import EvaluationScoreRequest
    from app.services.audit_service import score_evaluation
    init_db()
    with db() as connection:
        connection.execute("INSERT INTO evaluations (id, model_provider, model_name, validation_status, latency_ms, estimated_cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", ("bad", "ollama", "qwen", "completed_with_evidence_mismatch", 1, 0, "now"))
    result = score_evaluation("bad", EvaluationScoreRequest(correctness=2))
    assert result["scoring_allowed"] is False
    assert "not methodologically valid" in result["error"]



def test_prompt_order_separates_retrieval_rank_from_priority():
    from app.services.project_service import _order_prompt_evidence
    unrelated = _chunk("auth", "jwt helper", retrieval_rank=1, final_score=2.0)
    assignment = _chunk("users", 'User.withUsername("demo").authorities("READ")', retrieval_rank=7, final_score=1.0)
    target_a = _chunk("get", "@GetMapping @PreAuthorize", retrieval_rank=8, selected_file_match=True, http_method="GET", method_route="")
    target_b = _chunk("post", "@PostMapping @PreAuthorize", retrieval_rank=9, selected_file_match=True, http_method="POST", method_route="")
    ordered = _order_prompt_evidence([unrelated, assignment, target_b, target_a], {"needs_endpoint_declarations", "needs_user_assignments"})
    assert [item["chunk_id"] for item in ordered] == ["get", "post", "users", "auth"]
    assert ordered[0]["retrieval_rank"] == 8
    assert ordered[0]["prompt_position"] == 1
    assert ordered[0]["evidence_priority_class"] == "target_primary"


def test_unsupported_route_claim_is_flagged_without_rewriting():
    from app.services.audit_service import _unsupported_route_claims
    evidence = [_chunk("route", "@GetMapping", effective_route="/", route_resolution_status="resolved")]
    answer = "The endpoint is probably /products."
    warnings = _unsupported_route_claims(answer, evidence)
    assert answer == "The endpoint is probably /products."
    assert warnings[0]["code"] == "unsupported_route_claim"
    assert warnings[0]["claim"] == "/products"


def test_compare_keeps_unavailable_selected_provider_as_failure_result(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    package = {"source_chunks": [_chunk("one")], "wiki_chunks": [], "diagnostics": {}}
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: name == "ollama")

    class Provider:
        name = "ollama"
        async def generate(self, messages, model): return {"content": "ok", "validation_status": "valid_simple", "diagnostics": {}}
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), "qwen"))
    result = asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=["ollama", "openai"])))
    assert [item["provider"] for item in result["results"]] == ["ollama", "openai"]
    assert result["results"][1]["validation_status"] == "provider_unavailable"
    assert result["excluded_providers"] == [{"provider": "openai", "reason": "Provider is unavailable or not configured."}]
    with db() as connection:
        rows = connection.execute("SELECT model_provider FROM evaluations").fetchall()
    assert [row["model_provider"] for row in rows] == ["ollama", "openai"]
    assert "provider" not in result["run_summary"]
