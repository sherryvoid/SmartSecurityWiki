# READ SUMMARY: This test module verifies the current backend pipeline from DB setup through indexing, chat, wiki, feedback, and imports.
# CHANGED: Updated the comparison mock to target the new shared retrieval-package function.
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


def test_file_filtering_includes_python_and_javascript_and_ignores_dependency_dirs(tmp_path):
    from app.services.files import is_relevant_file, language_for_path

    python_file = tmp_path / "app.py"
    python_file.write_text("def read_user(): pass")
    ts_file = tmp_path / "auth.ts"
    ts_file.write_text("export const requireRole = () => true")
    venv_file = tmp_path / "venv" / "lib" / "site.py"
    venv_file.parent.mkdir(parents=True)
    venv_file.write_text("ignored")
    node_file = tmp_path / "node_modules" / "pkg" / "index.js"
    node_file.parent.mkdir(parents=True)
    node_file.write_text("ignored")
    next_file = tmp_path / ".next" / "server.js"
    next_file.parent.mkdir()
    next_file.write_text("ignored")

    assert is_relevant_file(python_file)
    assert is_relevant_file(ts_file)
    assert language_for_path("app.py") == "python"
    assert language_for_path("auth.ts") == "typescript"
    assert not is_relevant_file(venv_file)
    assert not is_relevant_file(node_file)
    assert not is_relevant_file(next_file)


def test_parser_creates_python_function_async_function_and_class_chunks():
    from app.services.parser import chunk_source

    code = """
class AuthService:
    def require_role(self, user):
        if not user.is_admin:
            raise HTTPException(status_code=403)

async def get_current_user(token):
    return decode_token(token)
""".strip()
    chunks = chunk_source("auth.py", "python", code)
    by_name = {chunk.symbol_name: chunk for chunk in chunks}

    assert by_name["AuthService"].chunk_type == "class"
    assert by_name["require_role"].chunk_type == "function"
    assert by_name["get_current_user"].chunk_type == "async_function"
    assert all(chunk.start_line > 0 and chunk.start_line <= chunk.end_line for chunk in chunks)


def test_parser_creates_javascript_typescript_function_arrow_and_route_chunks():
    from app.services.parser import chunk_source

    code = """
export function requireAuth(req, res, next) {
  return next();
}

const requireRole = (role) => {
  return (req, res, next) => next();
};

router.post("/admin", requireAuth, requireRole("admin"), (req, res) => {
  res.send("ok");
});
""".strip()
    chunks = chunk_source("routes.ts", "typescript", code)
    chunk_types = [chunk.chunk_type for chunk in chunks]
    names = [chunk.symbol_name for chunk in chunks]

    assert "requireAuth" in names
    assert "requireRole" in names
    assert "route_handler" in chunk_types
    assert all(chunk.start_line > 0 and chunk.start_line <= chunk.end_line for chunk in chunks)


def test_security_detection_tags_fastapi_and_express_patterns():
    from app.services.security_detection import detect_security_tags

    fastapi_code = "@router.get('/admin', dependencies=[Depends(require_role)])\nraise HTTPException(status_code=status.HTTP_403_FORBIDDEN)"
    express_code = "router.post('/admin', requireAuth, requireRole('admin')); jwt.verify(token, secret);"

    assert "potential_access_check" in detect_security_tags(fastapi_code, "auth.py")
    assert "potential_entry_point" in detect_security_tags(fastapi_code, "auth.py")
    assert "potential_access_check" in detect_security_tags(express_code, "auth.ts")
    assert "potential_entry_point" in detect_security_tags(express_code, "auth.ts")


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

    monkeypatch.setattr(audit_service, "retrieve_evidence_package", lambda *args, **kwargs: {"source_chunks": shared_evidence, "wiki_chunks": [], "chunk_ids": ["c1"], "retrieval_log": ""})
    monkeypatch.setattr(audit_service, "provider_for", lambda name: (FakeProvider(), f"{name}-model"))

    result = asyncio.run(audit_service.compare_models("project-1", CompareRequest(question="Where?", providers=["ollama", "openai"])))

    assert len(result["results"]) == 2
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    assert "Evidence 1" in prompts[0]


def test_indexing_python_and_javascript_files_creates_chunks(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="fixture", source_type="github"))
    repo = Path(project["local_path"])
    (repo / "app").mkdir()
    (repo / "app" / "auth.py").write_text("def require_role(user):\n    return user.is_admin\n")
    (repo / "app" / "routes.ts").write_text("router.post('/admin', requireRole('admin'), handler);\n")

    project_service.index_project(project["id"])

    with db() as connection:
        files = [row["file_path"] for row in connection.execute("SELECT file_path FROM files WHERE project_id = ?", (project["id"],)).fetchall()]
        chunks = [dict(row) for row in connection.execute("SELECT c.*, f.file_path FROM code_chunks c JOIN files f ON f.id = c.file_id WHERE c.project_id = ?", (project["id"],)).fetchall()]

    assert "app/auth.py" in files
    assert "app/routes.ts" in files
    assert any(chunk["symbol_name"] == "require_role" for chunk in chunks)
    assert any(chunk["file_path"] == "app/routes.ts" for chunk in chunks)


def test_discovery_returns_python_and_jwt_security_fixtures(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="fixture", source_type="github"))
    repo = Path(project["local_path"])
    (repo / "security.py").write_text("def require_role(user):\n    return user.role == 'admin'\n")
    (repo / "auth.ts").write_text("export const validateJwt = (token) => jwt.verify(token, secret);\n")
    project_service.index_project(project["id"])

    role_results = project_service.discover_security_modules(project["id"], "role based access control endpoints")
    jwt_results = project_service.discover_security_modules(project["id"], "jwt validation")

    assert any(result["module_path"] == "security.py" for result in role_results)
    assert any(result["module_path"] == "auth.ts" for result in jwt_results)


def test_gemini_empty_default_model_resolves_to_flash(isolated_env, monkeypatch):
    import asyncio

    from app.core.config import get_settings
    from app.services import model_health

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("GEMINI_DEFAULT_MODEL", "")
    get_settings.cache_clear()

    async def fake_ollama(settings):
        return {"status": "Ollama not running"}

    monkeypatch.setattr(model_health, "_ollama_health", fake_ollama)
    settings = get_settings()
    health = asyncio.run(model_health.models_health(settings))

    assert settings.resolved_gemini_default_model == "gemini-2.5-flash"
    assert health["gemini"]["status"] == "Ready"
    assert health["gemini"]["default_model"] == "gemini-2.5-flash"


def test_subfolder_import_indexes_only_selected_subfolder(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="fixture", source_type="github", subfolder_path="services/api"))
    repo = Path(project["local_path"])
    (repo / "services" / "api").mkdir(parents=True)
    (repo / "services" / "api" / "auth.py").write_text("def require_role(user):\n    return user.role\n")
    (repo / "other").mkdir()
    (repo / "other" / "auth.py").write_text("def ignored():\n    return True\n")

    project_service.index_project(project["id"])

    with db() as connection:
        files = [row["file_path"] for row in connection.execute("SELECT file_path FROM files WHERE project_id = ?", (project["id"],)).fetchall()]

    assert files == ["services/api/auth.py"]


def test_missing_subfolder_marks_project_failed(isolated_env):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    project = project_service.create_project(ProjectCreate(name="fixture", source_type="github", subfolder_path="missing/path"))

    project_service.index_project(project["id"])
    status = project_service.project_status(project["id"])

    assert status["status"] == "failed"
    assert "Selected subfolder does not exist" in status["status_message"]
