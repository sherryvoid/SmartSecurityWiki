import json


def _evidence(chunk_id="issuer", symbol="issueToken", cls="CredentialIssuer"):
    return {"chunk_id": chunk_id, "file_path": f"src/{cls}.java", "class_name": cls, "symbol_name": symbol, "start_line": 10, "end_line": 30, "code_snippet": "return token;", "language": "java"}


def test_typed_reference_accepts_enclosing_class_and_method_and_rejects_wrong_type():
    from app.services.methodology import validate_model_references

    valid = validate_model_references("CredentialIssuer delegates to issueToken.", [_evidence()])
    invalid = validate_model_references("CredentialIsssuer delegates token creation.", [_evidence()])
    assert valid["model_reference_validation_status"] == "valid"
    assert {item["reference_type"] for item in valid["typed_model_references"]} == {"type_or_class", "method_or_function"}
    assert invalid["model_reference_validation_status"] == "invalid"


def test_usage_normalization_preserves_null_for_unsupported_fields():
    from app.services.usage_service import normalize_usage

    ollama = normalize_usage("ollama", {"prompt_eval_count": 101, "eval_count": 22, "load_duration": 2_000_000, "eval_duration": 8_000_000})
    gemini = normalize_usage("gemini", {"promptTokenCount": 90, "candidatesTokenCount": 10, "totalTokenCount": 100, "cachedContentTokenCount": 5})
    openai = normalize_usage("openai", {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100, "prompt_tokens_details": {"cached_tokens": 12}, "completion_tokens_details": {"reasoning_tokens": 4}})
    assert ollama["provider_reported_total_tokens"] == 123 and ollama["provider_reported_cached_input_tokens"] is None
    assert gemini["provider_reported_cached_input_tokens"] == 5 and gemini["provider_reported_reasoning_tokens"] is None
    assert openai["provider_reported_cached_input_tokens"] == 12 and openai["provider_reported_reasoning_tokens"] == 4


def test_component_measurement_characters_and_utf8_bytes_are_exact():
    from app.services.usage_service import measure_prompt_components

    source = [{**_evidence(), "code_snippet": "return \"tökén\";"}]
    wiki = [{"content": "Résumé", "title": "T", "section_title": "S", "module_id": "M"}]
    messages = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Question: why?"}]
    measured = measure_prompt_components(messages, "why?", source, wiki, lambda items: "META\n" + items[0]["content"])
    assert measured["primary_source_content"]["characters"] == len('return "tökén";')
    assert measured["primary_source_content"]["utf8_bytes"] == len('return "tökén";'.encode("utf-8"))
    assert measured["wiki_context_content"]["utf8_bytes"] == len("Résumé".encode("utf-8"))
    assert measured["system_instructions"]["token_count_type"] == "estimated"


def test_usage_persistence_survives_reload_and_cost_is_unavailable_without_pricing(isolated_env):
    from app.db.database import init_db
    from app.services.usage_service import normalize_usage, persist_usage, usage_summary

    init_db()
    source = [_evidence()]
    persist_usage(execution_id="exec", run_id="run", project_id="p", operation="ask", provider="gemini", model="gemini-test", normalized=normalize_usage("gemini", {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10}), duration_ms=12, composition={}, supplied_source=source, cited_source=[], wiki=[], source_hash="s", wiki_hash="w", status="completed")
    summary = usage_summary("p")
    row = summary["recent_executions"][0]
    assert row["provider_reported_total_tokens"] == 10
    assert row["api_cost"] is None
    assert row["supplied_source_chunk_ids"] == ["issuer"] and row["cited_source_chunk_ids"] == []


def test_active_provider_registry_excludes_deepseek_and_keeps_historical_rows_readable(isolated_env):
    from app.db.database import db, init_db
    from app.services.llm import active_provider_names, provider_for
    from app.services.methodology import list_formal_runs

    init_db()
    assert "deepseek" not in active_provider_names()
    try:
        provider_for("deepseek")
        assert False, "inactive provider should not resolve"
    except ValueError:
        pass
    with db() as connection:
        connection.execute("INSERT INTO formal_runs (run_id,project_id,operation,timestamp,provider_model_json) VALUES ('old','p','ask','now',?)", (json.dumps({"provider":"deepseek","model":"deepseek-chat"}),))
    assert list_formal_runs("p")[0]["run_id"] == "old"


def test_formal_run_keeps_supplied_and_cited_packages_separate(isolated_env):
    from app.db.database import db, init_db
    from app.services.methodology import persist_formal_run

    init_db()
    supplied = [_evidence(), _evidence("mapper", "mapClaim", "PermissionMapper")]
    cited = supplied[:1]
    persist_formal_run({"run_id":"r","project_id":"p","operation":"ask","supplied_source_evidence":supplied,"cited_source_evidence":cited,"primary_evidence":cited})
    with db() as connection:
        row = connection.execute("SELECT supplied_source_evidence_json,cited_source_evidence_json,primary_evidence_json FROM formal_runs WHERE run_id='r'").fetchone()
    assert len(json.loads(row["supplied_source_evidence_json"])) == 2
    assert len(json.loads(row["cited_source_evidence_json"])) == len(json.loads(row["primary_evidence_json"])) == 1


def test_gemini_usage_normalizes_sdk_snake_case_and_missing_is_unavailable():
    from app.services.usage_service import normalize_usage
    actual = normalize_usage("gemini", {"prompt_token_count": 101, "candidates_token_count": 23, "total_token_count": 130, "cached_content_token_count": 7, "thoughts_token_count": 6})
    assert (actual["provider_reported_input_tokens"], actual["provider_reported_output_tokens"], actual["provider_reported_total_tokens"]) == (101, 23, 130)
    assert actual["provider_reported_cached_input_tokens"] == 7
    assert actual["provider_reported_reasoning_tokens"] == 6
    assert normalize_usage("gemini", {})["usage_source"] == "unavailable"


def test_compact_serializer_is_deterministic_reduced_and_keeps_source_verbatim():
    from app.services.project_service import compact_evidence_to_prompt, evidence_to_prompt
    evidence = [{"chunk_id":"internal-uuid", "file_path":"src/CredentialIssuer.java", "class_name":"CredentialIssuer", "symbol_name":"issue", "start_line":10, "end_line":12, "evidence_priority_class":"claim_population", "code_snippet":"return issue(subject);", "retrieval_rank":99}]
    first = compact_evidence_to_prompt(evidence)
    assert first == compact_evidence_to_prompt(evidence)
    assert "return issue(subject);" in first and "[E1]" in first and "CredentialIssuer" in first
    assert "internal-uuid" not in first and "Retrieval Rank" not in first
    assert len(first) < len(evidence_to_prompt(evidence))


def test_evidence_aliases_map_back_to_chunk_ids():
    from app.services.audit_service import parse_chat_answer
    evidence = [{"chunk_id":"chunk-a"},{"chunk_id":"chunk-b"}]
    parsed, status, invalid = parse_chat_answer('{"answer":"grounded","confidence":"high","evidence_refs":["E2"],"helper_chain":[],"limitations":[],"needs_review":false}', evidence)
    assert status == "valid_json" and invalid == [] and parsed.evidence_refs == ["chunk-b"]


def test_named_baselines_are_insert_only(isolated_env):
    import pytest
    from app.db.database import init_db
    from app.services.methodology import persist_evaluation_baseline
    init_db()
    persist_evaluation_baseline("before", "project", "pre", {"value":1})
    with pytest.raises(ValueError, match="immutable"):
        persist_evaluation_baseline("before", "project", "pre", {"value":2})
