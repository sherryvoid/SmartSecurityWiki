import json
import asyncio

import pytest


def _evidence():
    return [{
        "chunk_id": "chunk-1",
        "file_path": "src/WorkspaceService.kt",
        "symbol_name": "initializeWorkspace",
        "start_line": 1,
        "end_line": 20,
        "code_snippet": "fun initializeWorkspace() = Unit",
    }]


def _payload(answer="Structured answer", refs=None):
    return {
        "answer": answer,
        "confidence": "high",
        "access_control_summary": None,
        "evidence_refs": refs if refs is not None else ["E1"],
        "helper_chain": [],
        "limitations": [],
        "needs_review": False,
    }


@pytest.mark.parametrize("raw", [
    lambda value: value,
    lambda value: f"```json\n{value}\n```",
    lambda value: f"Leading prose.\n\n{value}",
    lambda value: f"{value}\n\nTrailing prose.",
    lambda value: f"A prose answer first.\n\n```json\n{value}\n```",
])
def test_valid_structured_object_is_recovered_from_common_provider_shapes(raw):
    from app.services.audit_service import parse_chat_answer

    parsed, status, invalid = parse_chat_answer(raw(json.dumps(_payload())), _evidence())

    assert parsed.answer == "Structured answer"
    assert parsed.evidence_refs == ["chunk-1"]
    assert status == "valid_json"
    assert invalid == []


def test_multiple_json_objects_are_schema_validated_instead_of_selected_by_position_or_size():
    from app.services.audit_service import parse_chat_answer

    unrelated = json.dumps({"answer": "wrong shape", "nested": {"large": [1, 2, 3]}})
    valid = json.dumps(_payload("Correct schema object"))
    parsed, status, _ = parse_chat_answer(f"{unrelated}\nprose\n{valid}\n{{truncated", _evidence())

    assert status == "valid_json"
    assert parsed.answer == "Correct schema object"


@pytest.mark.parametrize("raw", [
    "A complete plain-text answer with no JSON.",
    "A useful answer.\n\n{malformed JSON",
    '{"answer": "truncated", "confidence": "high"',
    "The code uses mapOf(\"ownerId\" to uid) { not JSON }.",
    "## Answer\nUseful prose.\n\n**answer**: A second representation.\n**confidence**: high\n**evidence_refs**: [E1]",
    "<think>removed upstream</think>\nA usable local-style answer.",
])
def test_usable_text_is_preserved_when_no_schema_valid_object_exists(raw):
    from app.services.audit_service import parse_chat_answer

    parsed, status, invalid = parse_chat_answer(raw, _evidence())

    assert parsed is None
    assert status == "text_fallback"
    assert invalid == []
    assert raw


def test_empty_content_is_terminal_and_not_treated_as_text_fallback():
    from app.services.audit_service import parse_chat_answer

    parsed, status, invalid = parse_chat_answer("   ", _evidence())

    assert parsed is None
    assert status == "empty_response"
    assert invalid == []


def test_q7_prose_plus_markdown_fields_replay_preserves_full_answer_and_has_terminal_diagnostics():
    from app.services.audit_service import _execution_details, _record_chat_parse_outcome, parse_chat_answer

    raw = (
        "Dunio creates the household and first owner in one WriteBatch [E1].\n\n"
        "---\n\n**answer**: The batch creates all records and commits once.\n"
        "**confidence**: high\n**access_control_summary**: Rules bind the owner to the caller.\n"
        "**evidence_refs**: [E1]\n**helper_chain**: []\n**limitations**: []\n**needs_review**: false"
    )
    parsed, status, _ = parse_chat_answer(raw, _evidence())
    result = {"diagnostics": {"processing": {"response_received": True, "content_present": True, "content_length": len(raw)}}}
    _record_chat_parse_outcome(result, status, True)
    details = _execution_details("id", "start", "ask", status, "q", "q", None, 1, _evidence(), [], "gemini", "model", 1, result["diagnostics"])

    assert parsed is None
    assert status == "text_fallback"
    assert raw.startswith("Dunio creates")
    assert details["status"] == "completed_with_warnings"
    assert details["processing"]["parse_status"] == "text_fallback"
    assert details["processing"]["schema_validation_status"] == "failed_text_preserved"
    assert details["processing"]["evidence_validation_status"] == "valid"


def test_ask_text_fallback_preserves_display_persistence_and_terminal_status(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ChatRequest
    from app.services import audit_service

    init_db()
    raw = "Complete cloud prose answer [E1].\n\n**answer**: duplicate Markdown representation"
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *a, **k: {
        "source_chunks": _evidence(), "wiki_chunks": [], "wiki_context": {}, "diagnostics": {"expanded_query": "q"},
    })

    class Provider:
        name = "gemini"

        async def generate(self, messages, model):
            return {"content": raw, "ok": True, "diagnostics": {"processing": {"response_received": True, "content_present": True, "content_length": len(raw)}}}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), "fixture-model"))
    result = asyncio.run(audit_service.chat("project", ChatRequest(question="q", provider="gemini")))

    assert result["answer"] == raw
    assert result["validation_status"] == "completed_with_warnings"
    assert result["execution"]["status"] == "completed_with_warnings"
    assert result["execution"]["processing"]["parse_status"] == "text_fallback"
    assert result["execution"]["processing"]["schema_validation_status"] == "failed_text_preserved"
    with db() as connection:
        stored = connection.execute("SELECT content,raw_model_response,parsed_answer_json,validation_status FROM chat_messages WHERE id=?", (result["message_id"],)).fetchone()
    assert tuple(stored) == (raw, raw, None, "completed_with_warnings")


def test_compare_text_fallback_preserves_full_answer(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service

    init_db()
    raw = "Provider-neutral plain answer [E1] with no JSON."
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *a, **k: {
        "source_chunks": _evidence(), "wiki_chunks": [], "wiki_context": {}, "diagnostics": {"expanded_query": "q"},
    })
    monkeypatch.setattr(audit_service, "is_provider_available", lambda name: True)

    class Provider:
        name = "openrouter"

        async def generate(self, messages, model):
            return {"content": raw, "ok": True, "diagnostics": {"processing": {"response_received": True, "content_present": True, "content_length": len(raw)}}}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (Provider(), "fixture-model"))
    result = asyncio.run(audit_service.compare_models("project", CompareRequest(question="q", providers=["openrouter"])))
    item = result["results"][0]

    assert item["answer"] == item["full_answer"] == item["answer_preview"] == raw
    assert item["validation_status"] == "completed_with_warnings"
    assert item["execution"]["processing"]["parse_status"] == "text_fallback"
