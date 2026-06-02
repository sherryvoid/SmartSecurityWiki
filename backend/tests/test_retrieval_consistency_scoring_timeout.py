# READ SUMMARY: This test module covers shared retrieval, query-time scoring, display status mapping, and timeout handling.
# CHANGED: Added regression tests for Ask/Compare evidence consistency, source-code weighting, status labels, and graceful model timeout behavior.
import asyncio
import json

import httpx
import pytest


def _insert_project_chunks():
    from app.db.database import db

    project_id = "project-retrieval"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("file-1", project_id, "src/WebSecurityConfig.java", "java", 100, 10, 1, "now"),
        )
        connection.execute(
            """
            INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("file-2", project_id, "README.md", "markdown", 100, 10, 1, "now"),
        )
        rows = [
            (
                "c1",
                "file-1",
                "method",
                "configure",
                "WebSecurityConfig",
                1,
                8,
                "requestMatchers('/admin').hasRole('ADMIN');",
                "permission_check,jwt",
            ),
            (
                "c2",
                "file-2",
                "markdown_section",
                "Access control",
                None,
                1,
                6,
                "This README describes the access control check.",
                "",
            ),
        ]
        for chunk_id, file_id, chunk_type, symbol, class_name, start, end, code, tags in rows:
            connection.execute(
                """
                INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, project_id, file_id, chunk_type, symbol, class_name, start, end, code, tags, f"code:{chunk_id}", "now"),
            )
    return project_id


def test_ask_and_compare_retrieve_same_chunks(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import ChatRequest, CompareRequest
    from app.services import audit_service, project_service

    init_db()
    project_id = _insert_project_chunks()
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])

    class FakeProvider:
        async def generate(self, messages, model, temperature=0.1):
            return {
                "content": json.dumps(
                    {
                        "answer": "Access control is configured in the security method.",
                        "confidence": "high",
                        "access_control_summary": "Uses request matchers and role checks.",
                        "evidence_refs": ["c1", "c2"],
                        "helper_chain": [],
                        "limitations": [],
                        "needs_review": False,
                    }
                ),
                "raw": {},
                "ok": True,
            }

    monkeypatch.setattr(audit_service, "provider_for", lambda name: (FakeProvider(), f"{name}-model"))

    chat_result = asyncio.run(audit_service.chat(project_id, ChatRequest(question="access control check", provider="ollama")))
    compare_result = asyncio.run(audit_service.compare_models(project_id, CompareRequest(question="access control check", providers=["ollama"])))
    chat_ids = [item["chunk_id"] for item in chat_result["evidence"]]
    compare_ids = [item["chunk_id"] for item in compare_result["evidence"]]

    assert set(chat_ids) == set(compare_ids)
    assert chat_ids == compare_ids


def test_rescore_prefers_source_over_readme():
    from app.services.vector_index import rescore_chunks

    chunk_a = {"base_similarity": 0.8, "file_path": "WebSecurityConfig.java", "chunk_type": "method", "tags": ["permission_check"]}
    chunk_b = {"base_similarity": 0.8, "file_path": "README.md", "chunk_type": "markdown_section", "tags": []}

    rescored = rescore_chunks([chunk_b, chunk_a])

    assert rescored[0]["file_path"] == "WebSecurityConfig.java"
    assert rescored[0]["final_score"] > rescored[1]["final_score"]


@pytest.mark.parametrize(
    ("path", "chunk_type", "expected"),
    [
        ("A.java", "function", 0.8 * 1.3 * 1.2),
        ("A.go", "function", 0.8 * 1.3 * 1.2),
        ("A.py", "function", 0.8 * 1.3 * 1.2),
        ("A.ts", "function", 0.8 * 1.2 * 1.2),
        ("A.kt", "function", 0.8 * 1.3 * 1.2),
        ("A.rs", "function", 0.8 * 1.3 * 1.2),
        ("README.md", "markdown_section", 0.8 * 0.7 * 0.6),
        ("IAccountManager.aidl", "function", 0.8 * 1.2 * 1.2),
        ("account_policy.te", "function", 0.8 * 1.2 * 1.2),
    ],
)
def test_rescore_file_extension_weights(path, chunk_type, expected):
    from app.services.vector_index import rescore_chunks

    result = rescore_chunks([{"base_similarity": 0.8, "file_path": path, "chunk_type": chunk_type, "tags": []}])[0]

    assert result["final_score"] == pytest.approx(expected, abs=0.01)


def test_rescore_security_tag_boost():
    from app.services.vector_index import rescore_chunks

    no_tags = rescore_chunks([{"base_similarity": 0.8, "file_path": "auth", "chunk_type": "unknown", "tags": []}])[0]
    one_tag = rescore_chunks([{"base_similarity": 0.8, "file_path": "auth", "chunk_type": "unknown", "tags": ["checkPermission"]}])[0]
    two_tags = rescore_chunks([{"base_similarity": 0.8, "file_path": "auth", "chunk_type": "unknown", "tags": ["role", "jwt"]}])[0]

    assert no_tags["final_score"] == pytest.approx(0.8, abs=0.01)
    assert one_tag["final_score"] == pytest.approx(0.95, abs=0.01)
    assert two_tags["final_score"] == pytest.approx(0.95, abs=0.01)


def test_rescore_missing_metadata_no_crash():
    from app.services.vector_index import rescore_chunks

    result = rescore_chunks([{"metadata": {}, "base_similarity": 0.8}])[0]

    assert result["final_score"] == pytest.approx(0.8, abs=0.01)


def test_display_status_mapping():
    from app.db.schemas import DISPLAY_STATUS_MAP, display_status_for

    for key, label in DISPLAY_STATUS_MAP.items():
        assert label != key
        assert display_status_for(key) == label
    assert display_status_for("unknown_xyz") == "Answer generated"


def test_ollama_timeout_returns_graceful_response(isolated_env, monkeypatch):
    from app.core.config import get_settings
    from app.services.llm import OllamaProvider

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    provider = OllamaProvider(get_settings())

    result = asyncio.run(provider.generate([{"role": "user", "content": "Where?"}], "qwen"))

    assert result["validation_status"] == "timeout"
    assert result["display_status"] == "Model timed out"


def test_compare_continues_after_timeout(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import CompareRequest
    from app.services import audit_service

    init_db()
    evidence = [{"chunk_id": "c1", "file_path": "A.java", "symbol_name": "m", "start_line": 1, "end_line": 2, "security_tags": "", "code_snippet": "checkPermission();"}]
    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: {"source_chunks": evidence, "wiki_chunks": [], "chunk_ids": ["c1"], "retrieval_log": ""})

    class TimeoutProvider:
        async def generate(self, messages, model, temperature=0.1):
            return audit_service.timeout_response()

    class ValidProvider:
        async def generate(self, messages, model, temperature=0.1):
            return {
                "content": json.dumps(
                    {
                        "answer": "Valid answer.",
                        "confidence": "high",
                        "access_control_summary": "Summary.",
                        "evidence_refs": ["c1"],
                        "helper_chain": [],
                        "limitations": [],
                        "needs_review": False,
                    }
                ),
                "raw": {},
                "ok": True,
            }

    def fake_provider_for(name):
        if name == "ollama":
            return TimeoutProvider(), "ollama-model"
        return ValidProvider(), "gemini-model"

    monkeypatch.setattr(audit_service, "provider_for", fake_provider_for)

    result = asyncio.run(audit_service.compare_models("project-1", CompareRequest(question="Where?", providers=["ollama", "gemini"])))

    assert len(result["results"]) == 2
    assert result["results"][0]["validation_status"] == "timeout"
    assert result["results"][1]["answer"] == "Valid answer."
