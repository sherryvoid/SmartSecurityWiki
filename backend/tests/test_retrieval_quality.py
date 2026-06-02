from pathlib import Path
import shutil
import uuid

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "java_auth_sample" / "AuthService.java"
PROJECT_ID = "retrieval-quality-project"


@pytest.fixture
def indexed_java_auth_sample(monkeypatch, ephemeral_chroma):
    from app.core.config import get_settings
    from app.db.database import init_db
    from app.services import vector_index
    from app.services.parser import chunk_source

    temp_root = Path(__file__).resolve().parents[1] / ".retrieval_quality_tmp" / str(uuid.uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SQLITE_DB_PATH", str(temp_root / "security_codewiki.db"))
    monkeypatch.setenv("PROJECT_STORAGE_PATH", str(temp_root / "projects"))
    monkeypatch.setenv("CHROMA_DB_PATH", str(temp_root / "chroma"))
    monkeypatch.setenv("EXPORT_STORAGE_PATH", str(temp_root / "exports"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "sentence-transformers")
    get_settings.cache_clear()
    vector_index._embedding_provider = None
    vector_index._embedding_warning = None
    monkeypatch.setattr(vector_index, "_client", lambda: ephemeral_chroma)

    init_db()
    source_code = FIXTURE_PATH.read_text(encoding="utf-8")
    chunks = chunk_source("AuthService.java", "java", source_code)
    indexed_chunks = []
    for chunk in chunks:
        payload = {
            "id": chunk.chunk_id,
            "project_id": PROJECT_ID,
            "file_path": chunk.file_path,
            "language": chunk.language,
            "class_name": chunk.class_name,
            "symbol_name": chunk.symbol,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "code": chunk.content,
            "security_tags": ",".join(chunk.tags),
        }
        vector_index.index_code_chunk(payload)
        indexed_chunks.append(chunk)

    yield {"chunks": indexed_chunks, "vector_index": vector_index, "project_id": PROJECT_ID}

    get_settings.cache_clear()
    vector_index._embedding_provider = None
    vector_index._embedding_warning = None
    shutil.rmtree(temp_root, ignore_errors=True)


def test_semantic_chunks_exist_for_auth_service(indexed_java_auth_sample):
    vector_index = indexed_java_auth_sample["vector_index"]
    collection = vector_index._collection(indexed_java_auth_sample["project_id"])

    result = collection.get(where={"file_path": "AuthService.java"})
    metadatas = result.get("metadatas") or []

    assert len(metadatas) >= 2
    assert all("AuthService.java" in item["file_path"] for item in metadatas)
    assert {"checkPermission", "enforcePermission"}.issubset({item.get("symbol_name") for item in metadatas})


def test_security_tags_are_present_for_permission_and_exception_chunks(indexed_java_auth_sample):
    chunks = indexed_java_auth_sample["chunks"]

    check_permission = next(chunk for chunk in chunks if chunk.symbol == "checkPermission")
    exception_chunks = [chunk for chunk in chunks if "SecurityException" in chunk.content]

    assert check_permission.tags
    assert exception_chunks
    assert all(chunk.tags for chunk in exception_chunks)


def test_retrieval_returns_auth_service_for_permission_query(indexed_java_auth_sample):
    vector_index = indexed_java_auth_sample["vector_index"]

    results = vector_index.query(
        indexed_java_auth_sample["project_id"],
        "where is permission checked before access",
        limit=5,
        source_type="code",
    )
    debug_results = [
        {
            "id": hit.id,
            "file_path": hit.metadata.get("file_path"),
            "symbol": hit.metadata.get("symbol_name"),
            "document": hit.document[:300],
        }
        for hit in results
    ]

    assert any("AuthService.java" in hit.metadata.get("file_path", "") for hit in results), debug_results
    assert any(
        "checkPermission" in hit.metadata.get("symbol_name", "") or "checkPermission" in hit.document
        for hit in results
    ), debug_results


def test_embedding_mode_is_semantic(indexed_java_auth_sample):
    mode = indexed_java_auth_sample["vector_index"].get_embedding_mode()

    assert (
        mode == "semantic"
    ), "THESIS WARNING: Semantic embeddings are NOT active. Hash fallback is running. Fix this before thesis evaluation."
