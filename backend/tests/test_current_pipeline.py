import asyncio
import json
import sqlite3
import uuid
import zipfile
from pathlib import Path

import pytest


def test_db_schema_initialization(isolated_env):
    from app.core.config import get_settings
    from app.db.database import init_db

    init_db()
    connection = sqlite3.connect(get_settings().sqlite_db_path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    connection.close()

    assert {
        "projects",
        "files",
        "code_chunks",
        "wiki_pages",
        "chat_sessions",
        "chat_messages",
        "verifications",
        "evaluations",
    }.issubset(tables)


def test_zip_safe_extraction_allows_normal_zip(isolated_env, tmp_path):
    from app.services.project_service import _safe_extract_zip

    archive_path = tmp_path / "normal.zip"
    target = tmp_path / "out"
    target.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("src/App.java", "class App {}")

    with zipfile.ZipFile(archive_path) as archive:
        _safe_extract_zip(archive, target)

    assert (target / "src" / "App.java").read_text() == "class App {}"


def test_zip_safe_extraction_rejects_path_traversal(isolated_env, tmp_path):
    from app.services.project_service import _safe_extract_zip

    archive_path = tmp_path / "bad.zip"
    target = tmp_path / "out"
    target.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "nope")

    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError):
            _safe_extract_zip(archive, target)


def test_parser_creates_java_method_chunk():
    from app.services.parser import chunk_source

    code = """
public class Demo {
  public boolean checkAccess(String role) {
    return role.equals("ADMIN");
  }
}
""".strip()
    chunks = chunk_source("Demo.java", "java", code)
    method_chunks = [chunk for chunk in chunks if chunk.chunk_type == "method"]

    assert method_chunks
    assert all(chunk.start_line > 0 for chunk in method_chunks)
    assert all(chunk.start_line <= chunk.end_line for chunk in method_chunks)


def test_security_detection_assigns_tags_for_access_control_keywords():
    from app.services.security_detection import detect_security_tags

    code = """
http.authorizeHttpRequests().requestMatchers("/admin").hasRole("ADMIN");
checkPermission("account:read");
throw new SecurityException("denied");
"""
    tags = detect_security_tags(code, "SecurityConfig.java")

    assert tags
    assert "potential_access_check" in tags


def test_chat_no_evidence_returns_safe_not_verified(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import ChatRequest
    from app.services import audit_service

    init_db()
    monkeypatch.setattr(audit_service, "retrieve_evidence", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit_service, "retrieve_wiki_context", lambda *args, **kwargs: [])

    result = asyncio.run(audit_service.chat("project-1", ChatRequest(question="Where is auth?")))

    assert result["answer"] == "Not verified from the available source-code evidence."
    assert result["evidence"] == []


def test_retrieved_evidence_card_integrity(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "project-1"
    file_id = "file-1"
    chunk_id = "chunk-1"
    with db() as connection:
        connection.execute(
            "INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, project_id, "src/SecurityConfig.java", "java", 120, 8, 1, "now"),
        )
        connection.execute(
            """
            INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                project_id,
                file_id,
                "method",
                "configure",
                "SecurityConfig",
                10,
                18,
                "checkPermission();",
                "potential_access_check",
                "code:chunk-1",
                "now",
            ),
        )
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])

    evidence = project_service.retrieve_evidence(project_id, "checkPermission", limit=1)

    assert evidence
    card = evidence[0]
    assert card["chunk_id"] == chunk_id
    assert card["file_path"] == "src/SecurityConfig.java"
    assert card["start_line"] == 10
    assert card["end_line"] == 18
    assert "checkPermission" in card["code_snippet"]


def test_wiki_generation_stores_wiki_page(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import WikiGenerateRequest
    from app.services import audit_service

    init_db()

    class FakeProvider:
        async def generate(self, messages, model, temperature=0.1):
            return {"content": "# Security Wiki\n\nEvidence-backed notes.", "raw": {}, "ok": True}

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (FakeProvider(), "fake-model"))
    monkeypatch.setattr(audit_service, "retrieve_evidence", lambda *args, **kwargs: [{"chunk_id": "c1", "file_path": "A.java", "symbol_name": "m", "start_line": 1, "end_line": 2, "security_tags": "", "code_snippet": "checkPermission();"}])
    monkeypatch.setattr(audit_service, "index_wiki_page", lambda *args, **kwargs: ["wiki:1:0"])

    result = asyncio.run(audit_service.generate_wiki("project-1", WikiGenerateRequest(module_path="A.java")))

    with db() as connection:
        count = connection.execute("SELECT COUNT(*) FROM wiki_pages WHERE project_id = ?", ("project-1",)).fetchone()[0]
    assert count == 1
    assert result["content_markdown"].startswith("# Security Wiki")


def test_wiki_indexing_upserts_source_type_wiki(monkeypatch):
    from app.services import vector_index

    captured = {}

    class FakeCollection:
        def upsert(self, ids, documents, metadatas):
            captured["ids"] = ids
            captured["documents"] = documents
            captured["metadatas"] = metadatas

    monkeypatch.setattr(vector_index, "_collection", lambda project_id: FakeCollection())

    ids = vector_index.index_wiki_page("project-1", "wiki-1", "src/A.java", "Security Wiki", "# Overview\n\nAccess checks.")

    assert ids
    assert captured["metadatas"][0]["source_type"] == "wiki"
    assert captured["metadatas"][0]["project_id"] == "project-1"


def test_verification_manual_feedback_is_stored(isolated_env):
    from app.db.database import db, init_db
    from app.db.schemas import VerificationRequest
    from app.services.audit_service import verify

    init_db()
    result = verify(
        VerificationRequest(
            target_type="chat_message",
            target_id="message-1",
            verdict="Needs Review",
            human_comment="Manual audit feedback: evidence is incomplete.",
        )
    )

    with db() as connection:
        row = connection.execute("SELECT verdict, human_comment FROM verifications WHERE id = ?", (result["id"],)).fetchone()
    assert row["verdict"] == "Needs Review"
    assert "Manual audit feedback" in row["human_comment"]


def test_model_comparison_uses_shared_evidence_package(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service

    init_db()
    shared_evidence = [{"chunk_id": "c1", "file_path": "A.java", "symbol_name": "m", "start_line": 1, "end_line": 3, "security_tags": "", "code_snippet": "checkPermission();"}]
    prompts = []

    class FakeProvider:
        async def generate(self, messages, model, temperature=0.1):
            prompts.append(messages[-1]["content"])
            return {"content": json.dumps({"answer": "ok"}), "raw": {}, "ok": True}

    monkeypatch.setattr(audit_service, "retrieve_evidence", lambda *args, **kwargs: shared_evidence)
    monkeypatch.setattr(audit_service, "retrieve_wiki_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (FakeProvider(), f"{name}-model"))

    result = asyncio.run(audit_service.compare_models("project-1", CompareRequest(question="Where?", providers=["ollama", "openai"])))

    assert len(result["results"]) == 2
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    assert "Evidence 1" in prompts[0]
