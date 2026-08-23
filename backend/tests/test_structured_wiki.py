import asyncio
import json

import pytest
from pydantic import ValidationError


def minimal_wiki_payload() -> dict:
    return {
        "module_overview": "Access checks are enforced before returning admin data.",
        "entry_points": [
            {
                "name": "getAdmin",
                "file_path": "src/AdminController.java",
                "start_line": 10,
                "end_line": 20,
                "description": "Handles the admin endpoint.",
                "chunk_id": "chunk-1",
            }
        ],
        "access_control_matrix": [
            {
                "caller": "getAdmin",
                "permission_check": "hasRole('ADMIN')",
                "file_path": "src/AdminController.java",
                "start_line": 14,
                "chunk_id": "chunk-1",
            }
        ],
        "vertical_helpers": [
            {
                "name": "requireAdmin",
                "file_path": "src/Authz.java",
                "role": "Checks the caller role.",
                "chunk_id": "chunk-2",
            }
        ],
        "requirement_traces": [
            {
                "requirement": "Only admins may access the endpoint.",
                "code_reference": "hasRole('ADMIN')",
                "file_path": "src/AdminController.java",
                "chunk_id": "chunk-1",
            }
        ],
        "limitations": "Only the retrieved evidence was used.",
    }


def test_security_wiki_schema_accepts_valid_dict():
    from app.db.schemas import SecurityWikiSchema

    wiki = SecurityWikiSchema.model_validate(minimal_wiki_payload())

    assert wiki.module_overview.startswith("Access checks")
    assert wiki.entry_points[0].chunk_id == "chunk-1"


def test_security_wiki_schema_rejects_missing_required_field():
    from app.db.schemas import SecurityWikiSchema

    payload = minimal_wiki_payload()
    payload.pop("module_overview")

    with pytest.raises(ValidationError):
        SecurityWikiSchema.model_validate(payload)


def test_render_wiki_to_markdown_contains_entry_points_section():
    from app.db.schemas import SecurityWikiSchema
    from app.services.audit_service import render_wiki_to_markdown

    wiki = SecurityWikiSchema.model_validate(minimal_wiki_payload())
    markdown = render_wiki_to_markdown(wiki)

    assert "## Entry Points" in markdown
    assert "| Name | File | Lines | Description | Chunk ID |" in markdown


def test_fenced_wiki_json_is_stripped_and_parsed():
    from app.services.llm import parse_security_wiki_response

    fenced = "```json\n" + json.dumps(minimal_wiki_payload()) + "\n```"

    wiki = parse_security_wiki_response(fenced)

    assert wiki.entry_points[0].name == "getAdmin"


def test_invalid_wiki_json_returns_parse_failed_fallback():
    from app.services.llm import generate_structured_security_wiki

    class FakeProvider:
        async def generate(self, messages, model, temperature=0.1):
            return {"content": "this is not json", "raw": {}, "ok": True}

    wiki, raw_response, validation_status = asyncio.run(
        generate_structured_security_wiki(FakeProvider(), [{"role": "system", "content": "schema"}], "fake-model")
    )

    assert raw_response == "this is not json"
    assert validation_status == "invalid_json_fallback"
    assert wiki.model_dump()["parse_failed"] is True


def test_validate_wiki_chunk_ids_clears_invalid_helper_reference(monkeypatch):
    from app.db.schemas import SecurityWikiSchema
    from app.services import audit_service

    payload = minimal_wiki_payload()
    wiki = SecurityWikiSchema.model_validate(payload)
    monkeypatch.setattr(audit_service, "code_chunk_exists", lambda project_id, chunk_id: chunk_id == "chunk-1")

    audit_service.validate_wiki_chunk_ids("project-1", wiki)

    assert wiki.entry_points[0].chunk_id == "chunk-1"
    assert wiki.vertical_helpers[0].chunk_id is None
    assert "invalid generated chunk reference" in wiki.limitations


def test_validate_wiki_chunk_ids_clears_invalid_requirement_trace_reference(monkeypatch):
    from app.db.schemas import SecurityWikiSchema
    from app.services import audit_service

    payload = minimal_wiki_payload()
    payload["requirement_traces"][0]["chunk_id"] = "missing-requirement-chunk"
    wiki = SecurityWikiSchema.model_validate(payload)
    monkeypatch.setattr(audit_service, "code_chunk_exists", lambda project_id, chunk_id: chunk_id in {"chunk-1", "chunk-2"})

    audit_service.validate_wiki_chunk_ids("project-1", wiki)

    assert wiki.entry_points[0].chunk_id == "chunk-1"
    assert wiki.vertical_helpers[0].chunk_id == "chunk-2"
    assert wiki.requirement_traces[0].chunk_id is None
    assert "invalid generated chunk reference" in wiki.limitations