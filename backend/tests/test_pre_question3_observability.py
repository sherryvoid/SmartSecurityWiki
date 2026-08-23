import asyncio
import json


def _long_payload():
    summary = "## Decision path\n\n" + ("A generic permission mapper preserves every paragraph and inline citation (E1). " * 18)
    return {
        "answer": "Complete opening answer with inline evidence citation (E1).",
        "confidence": "high",
        "access_control_summary": summary,
        "evidence_refs": ["E1"],
        "helper_chain": [
            "1. Credential endpoint creates a claim (E1).",
            "2. Permission mapper converts the claim.\n\n- first bullet\n- second bullet",
        ],
        "limitations": ["No inferred behavior."],
        "needs_review": False,
    }


def test_gpt_equivalent_scenario_and_cached_actual_calculation():
    from app.services.usage_service import calculate_token_cost
    equivalent = calculate_token_cost(3607, 366, input_price=.15, output_price=.60)
    assert equivalent == 0.00076065
    actual_cached = calculate_token_cost(1000, 100, input_price=.15, cached_input_price=.075, output_price=.60, cached_tokens=400)
    assert actual_cached == 0.00018
    assert calculate_token_cost(None, 10, input_price=.15, output_price=.60) is None


def test_structured_answer_projection_preserves_all_sections():
    from app.services.methodology import render_structured_answer_payload
    payload = _long_payload()
    rendered = render_structured_answer_payload(payload)
    assert len(rendered) > 1500
    for expected in (payload["answer"], payload["access_control_summary"], payload["helper_chain"][0], payload["limitations"][0], "- first bullet"):
        assert expected.strip() in rendered


def test_compare_full_answer_persists_and_history_restores(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service
    from app.services.methodology import list_formal_runs, render_structured_answer_payload
    init_db()
    evidence = [{"chunk_id":"generic-1","file_path":"src/PermissionMapper.java","class_name":"PermissionMapper","symbol_name":"map","start_line":1,"end_line":8,"code_snippet":"return claims.stream();","security_tags":"authorization"}]
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *a,**k:{"source_chunks":evidence,"wiki_chunks":[],"diagnostics":{"expanded_query":"generic"},"wiki_context":{}})
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name:True)
    class Provider:
        name="gemini"
        async def generate(self,messages,model):
            raw=json.dumps(_long_payload())
            return {"content":raw,"ok":True,"usage":{"promptTokenCount":10,"candidatesTokenCount":20,"totalTokenCount":30},"diagnostics":{"processing":{"response_received":True,"content_present":True,"content_length":len(raw)}}}
    monkeypatch.setattr(audit_service,"provider_for",lambda name:(Provider(),"fixture-model"))
    result=asyncio.run(audit_service.compare_models("project",CompareRequest(question="Trace generic permission conversion",providers=["gemini"])))
    expected=render_structured_answer_payload(_long_payload())
    item=result["results"][0]
    assert item["full_answer"] == item["answer"] == expected
    assert item["answer_preview"] == _long_payload()["answer"]
    with db() as connection:
        persisted=json.loads(connection.execute("SELECT answer_json FROM formal_runs WHERE run_id=?",(result["run_summary"]["execution_id"],)).fetchone()[0])[0]
        evaluation=connection.execute("SELECT answer_text,parsed_answer_json FROM evaluations WHERE id=?",(item["evaluation_id"],)).fetchone()
    assert persisted["full_answer"] == evaluation["answer_text"] == expected
    restored=json.loads(list_formal_runs("project")[0]["answer_json"])[0]
    assert restored["full_answer"] == restored["answer"] == expected


def test_run_purpose_updates_usage_and_is_explicit(isolated_env):
    from app.db.database import db, init_db
    from app.services.methodology import persist_formal_run, update_run_purpose
    init_db(); persist_formal_run({"run_id":"run","project_id":"p","operation":"ask"})
    with db() as connection:
        assert connection.execute("SELECT run_purpose FROM formal_runs WHERE run_id='run'").fetchone()[0] == "development"
    update_run_purpose("p","run","formal_evaluation","Q-generic")
    with db() as connection:
        row=connection.execute("SELECT run_purpose,question_id FROM formal_runs WHERE run_id='run'").fetchone()
    assert tuple(row)==("formal_evaluation","Q-generic")


def test_usage_summary_returns_new_rows_and_versioned_scenario(isolated_env):
    from app.db.database import init_db
    from app.services.usage_service import normalize_usage,persist_usage,usage_summary
    init_db(); evidence=[{"chunk_id":"e"}]
    persist_usage(execution_id="new",run_id="run",project_id="p",operation="ask",provider="ollama",model="local-test",normalized=normalize_usage("ollama",{"prompt_eval_count":3607,"eval_count":366}),duration_ms=1,composition={},supplied_source=evidence,cited_source=evidence,wiki=[],source_hash="s",wiki_hash="w",status="completed")
    result=usage_summary("p")
    assert result["recent_executions"][0]["execution_id"] == "new"
    assert result["recent_executions"][0]["api_cost"] == 0
    assert result["recent_executions"][0]["gpt_equivalent_estimate"] == 0.00076065
    assert result["scenario_pricing"]["revision"] == "gpt-4o-mini-2024-07-18-v1"
