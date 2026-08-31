import json


class _Provider:
    name = "openrouter"


def _evidence():
    return [{"chunk_id": "chunk-1", "file_path": "src/ProductController.java", "symbol_name": "addProduct", "start_line": 32, "end_line": 36, "code_snippet": "@PreAuthorize(\"hasAnyAuthority('ASSISTANT_MANAGER','MANAGER','ADMIN')\")\nproductService.addProduct(product);"}]


def test_gpt51_gets_concise_presentation_layer_only():
    from app.services.audit_service import CHAT_JSON_PROMPT, _chat_messages_for_provider

    concise = _chat_messages_for_provider(_Provider(), "Who can add?", _evidence(), [], model="openai/gpt-5.1")
    historical = _chat_messages_for_provider(_Provider(), "Who can add?", _evidence(), [], model="openai/gpt-4o-mini")
    assert historical[0]["content"] == CHAT_JSON_PROMPT
    assert "gpt51-concise-v1" in concise[0]["content"]
    for rule in ("Answer completely but concisely", "Do not repeat the same fact", "Do not reproduce long source-code blocks", "1-3 sentences", "actual repository implementation/helper relationships", "material evidence gaps"):
        assert rule in concise[0]["content"]
    assert concise[1] == historical[1]


def test_gpt51_layer_preserves_schema_grounding_and_required_identifiers():
    from app.services.audit_service import _chat_messages_for_provider, parse_chat_answer

    prompt = _chat_messages_for_provider(_Provider(), "Who can add?", _evidence(), [], model="openai/gpt-5.1")[0]["content"]
    for field in ("answer", "confidence", "access_control_summary", "evidence_refs", "helper_chain", "limitations", "needs_review"):
        assert f'"{field}"' in prompt
    payload = {"answer": "ASSISTANT_MANAGER, MANAGER, or ADMIN may call ProductController.addProduct, which delegates to productService.addProduct [E1].", "confidence": "high", "access_control_summary": "The three authorities are required [E1].", "evidence_refs": ["E1"], "helper_chain": ["ProductController.addProduct -> ProductService.addProduct [E1]"], "limitations": ["The supplied evidence does not show a repository layer."], "needs_review": False}
    parsed, status, invalid = parse_chat_answer(json.dumps(payload), _evidence())
    assert status == "valid_json" and invalid == []
    assert parsed.model_dump().keys() >= payload.keys()
    assert "ASSISTANT_MANAGER" in parsed.answer and "ProductController.addProduct" in parsed.answer
    assert parsed.evidence_refs == ["chunk-1"]
    assert parsed.helper_chain == payload["helper_chain"] and parsed.limitations == payload["limitations"]


def test_configuration_version_is_recorded_without_changing_model_limits(isolated_env, monkeypatch):
    from app.core.config import get_settings
    from app.db.database import init_db
    from app.services.audit_service import _effective_model_configuration, presentation_configuration
    from app.services.usage_service import normalize_usage, persist_usage, usage_summary

    monkeypatch.setenv("OPENROUTER_MAX_OUTPUT_TOKENS", "2048")
    get_settings.cache_clear(); init_db()
    config = _effective_model_configuration("openrouter", "openai/gpt-5.1", 120)
    assert config["presentation_prompt_version"] == "gpt51-concise-v1"
    assert config["max_output_tokens"] == 2048 and config["temperature"] == 0.1
    assert presentation_configuration("openrouter", "openai/gpt-4o-mini") == {}
    persist_usage(execution_id="gpt51-concise", run_id="run", project_id="p", operation="ask", provider="openrouter", model="openai/gpt-5.1", normalized=normalize_usage("openrouter", {}), duration_ms=1, composition={}, supplied_source=[], cited_source=[], wiki=[], source_hash="s", wiki_hash="w", status="valid_json")
    saved = usage_summary("p")["recent_executions"][0]["model_configuration"]
    assert saved["presentation_prompt_version"] == "gpt51-concise-v1"
    assert saved["max_output_tokens"] == 2048
