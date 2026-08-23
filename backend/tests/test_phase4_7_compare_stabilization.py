import asyncio
import json


def _chunk():
    return {"chunk_id": "c1", "file_path": "src/ProductController.java", "symbol_name": "getProducts", "start_line": 8, "end_line": 12, "language": "java", "code_snippet": "void getProducts() {}"}


def _package():
    return {"source_chunks": [_chunk()], "wiki_chunks": [], "wiki_context": {"requested": True, "available_wiki_count": 0, "candidate_wiki_chunk_count": 0, "selected_wiki_chunk_count": 0, "selected_wiki_chunks": []}, "diagnostics": {}}


class Provider:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail

    async def generate(self, messages, model):
        if self.fail:
            return {"content": "secret provider detail", "ok": False, "raw": {"error": "provider_error"}, "diagnostics": {}}
        if self.name == "ollama":
            return {"content": "FILE: unknown\nLINE: unknown\nanswer", "ok": True, "validation_status": "valid_simple", "diagnostics": {}}
        return {"content": json.dumps({"answer": "answer", "confidence": "high", "evidence_refs": ["c1"], "helper_chain": [], "limitations": [], "needs_review": False}), "ok": True, "diagnostics": {}}


def _run(isolated_env, monkeypatch, providers, failed=None):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    init_db()
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: _package())
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(name, name == failed), f"{name}-model"))
    return asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=providers)))


def test_gemini_and_qwen_each_return_a_result(isolated_env, monkeypatch):
    result = _run(isolated_env, monkeypatch, ["gemini", "ollama"])
    assert [item["provider"] for item in result["results"]] == ["gemini", "ollama"]
    assert result["comparison_model_count"] == 2
    assert result["rq2_comparison_eligible"] is True


def test_one_selected_model_returns_one_result(isolated_env, monkeypatch):
    result = _run(isolated_env, monkeypatch, ["gemini"])
    assert len(result["results"]) == 1
    assert result["comparison_model_count"] == 1
    assert result["rq2_comparison_eligible"] is False


def test_three_ready_models_return_three_results(isolated_env, monkeypatch):
    result = _run(isolated_env, monkeypatch, ["gemini", "ollama", "deepseek"])
    assert [item["provider"] for item in result["results"]] == ["gemini", "ollama", "deepseek"]
    assert result["comparison_model_count"] == 3


def test_provider_failure_remains_visible_and_makes_run_ineligible(isolated_env, monkeypatch):
    result = _run(isolated_env, monkeypatch, ["gemini", "ollama"], failed="gemini")
    assert len(result["results"]) == 2
    failed = result["results"][0]
    assert failed["provider"] == "gemini"
    assert failed["validation_status"] == "provider_unavailable"
    assert "secret provider detail" not in failed["answer"]
    assert result["comparison_model_count"] == 2
    assert result["completed_model_count"] == 1
    assert result["rq2_comparison_eligible"] is False
