import asyncio
import json


def _evidence(chunk_id="c1"):
    return {"chunk_id": chunk_id, "file_path": "src/ProductController.java", "symbol_name": "ProductController", "start_line": 10, "end_line": 20, "language": "java", "code_snippet": "class ProductController {}"}


def test_many_wikis_are_semantically_filtered_deduplicated_and_capped(monkeypatch):
    from app.services import project_service
    class Hit:
        source_type = "wiki"
        distance = 0.1
        def __init__(self, index):
            self.id = f"wiki:w{index}:0"
            self.document = f"relevant section {index}"
            self.metadata = {"wiki_page_id": f"w{index}", "title": f"Wiki {index}", "section_title": "Authorization", "module_id": f"src/M{index}.java", "chunk_index": 0}
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [Hit(i) for i in range(10)])
    selected = project_service.retrieve_wiki_context("p", "authorization", limit=2)
    assert len(selected) == 2
    assert [item["retrieval_rank"] for item in selected] == [1, 2]
    assert all(item["source_type"] == "wiki" for item in selected)
    assert selected[0]["candidate_wiki_chunk_count"] == 10


def test_zero_wikis_and_primary_roles_are_independent(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service
    init_db()
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    with db() as connection:
        package = project_service.retrieve_evidence_package("missing", "who authorizes?", 10, connection)
    assert package["wiki_context"]["selected_wiki_chunk_count"] == 0
    assert package["wiki_context"]["selected_wiki_chunks"] == []
    assert "wiki" not in package["diagnostics"].get("satisfied_evidence_roles", [])


def test_model_reference_typo_is_not_fuzzy_corrected():
    from app.services.methodology import validate_model_references
    result = validate_model_references("FILE: ProductControler.java\nLINE: unknown", [_evidence()])
    assert result["model_reference_validation_status"] == "invalid"
    assert "invalid_model_file_reference" in {item["code"] for item in result["model_reference_warnings"]}


def test_rubric_schema_and_retrieval_separation():
    from app.db.schemas import EvaluationScoreRequest
    value = EvaluationScoreRequest(correctness=3, evidence_discipline=3, completeness=3, explanation_quality=3, source_reference_accuracy=2, hallucination=False, usefulness=3, verdict="Verified")
    assert value.correctness == 3
    assert "recall_at_k" not in value.model_dump()


def test_evaluation_config_hash_is_stable(isolated_env):
    from app.db.database import init_db
    from app.services.methodology import evaluation_configuration
    init_db()
    first = evaluation_configuration("p", [{"provider": "ollama", "model": "qwen"}])
    second = evaluation_configuration("p", [{"provider": "ollama", "model": "qwen"}])
    assert first["evaluation_config_hash"] == second["evaluation_config_hash"]


def test_durable_formal_run_restoration(isolated_env):
    from app.db.database import init_db
    from app.services.methodology import list_formal_runs, persist_formal_run
    init_db()
    persist_formal_run({"run_id": "run-1", "project_id": "p", "operation": "ask", "question": "q", "provider_model": {"provider": "ollama"}, "answer": "a", "primary_evidence": [_evidence()], "wiki_context": [], "execution_status": "completed", "evaluation_config_hash": "h"})
    restored = list_formal_runs("p")
    assert restored[0]["run_id"] == "run-1"
    assert json.loads(restored[0]["primary_evidence_json"])[0]["chunk_id"] == "c1"


def test_one_and_two_model_rq2_eligibility(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    wiki = [{"chunk_id": "wiki:w:0", "wiki_id": "w", "title": "W", "section": "S", "source_focus": "src/ProductController.java", "content": "orientation"}]
    package = {"source_chunks": [_evidence()], "wiki_chunks": wiki, "wiki_context": {"requested": True, "available_wiki_count": 1, "candidate_wiki_chunk_count": 1, "selected_wiki_chunk_count": 1, "selected_wiki_chunks": wiki}, "diagnostics": {}}
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)
    prompts = []
    class Provider:
        def __init__(self, name): self.name = name
        async def generate(self, messages, model):
            prompts.append(messages)
            if self.name == "ollama": return {"content": "FILE: unknown\nLINE: unknown\nanswer", "validation_status": "valid_simple", "diagnostics": {}}
            return {"content": json.dumps({"answer": "answer", "confidence": "high", "evidence_refs": ["c1"], "helper_chain": [], "limitations": [], "needs_review": False}), "diagnostics": {}}
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(name), "model"))
    one = asyncio.run(audit_service.compare_models("p", CompareRequest(question="q", providers=["ollama"])))
    two = asyncio.run(audit_service.compare_models("p", CompareRequest(question="q", providers=["ollama", "gemini"])))
    assert one["comparison_model_count"] == 1 and one["rq2_comparison_eligible"] is False
    assert "Single-model diagnostic" in one["comparison_invalid_reason"]
    assert two["comparison_model_count"] == 2 and two["rq2_comparison_eligible"] is True
    assert all(result["ordered_wiki_chunk_ids"] == ["wiki:w:0"] for result in two["results"])
    assert all("orientation" in str(messages) for messages in prompts)


def test_normalized_timeout_error(monkeypatch):
    from app.services.audit_service import timeout_response
    error = timeout_response()["error"]
    assert set(error) == {"error_code", "user_message", "technical_message", "retryable", "provider", "model", "execution_id"}
    assert error["error_code"] == "OLLAMA_TIMEOUT"
