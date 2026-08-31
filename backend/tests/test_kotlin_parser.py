from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kotlin_security_sample" / "AccessManager.kt"


def _fixture_chunks():
    from app.services.parser import chunk_source

    source = FIXTURE_PATH.read_text(encoding="utf-8")
    return source, chunk_source("src/AccessManager.kt", "kotlin", source)


def test_kotlin_tree_sitter_extracts_types_functions_modifiers_and_exact_lines():
    source, chunks = _fixture_chunks()
    by_symbol = {chunk.symbol: chunk for chunk in chunks}

    assert len(source.splitlines()) == 28
    assert by_symbol["AccessManager"].chunk_type in {"class", "constructor"}
    assert by_symbol["canModify"].class_name == "AccessManager"
    assert (by_symbol["canModify"].start_line, by_symbol["canModify"].end_line) == (4, 6)
    assert by_symbol["enforcePermission"].class_name == "AccessManager"
    assert (by_symbol["enforcePermission"].start_line, by_symbol["enforcePermission"].end_line) == (8, 13)
    assert "suspend fun enforcePermission" in by_symbol["enforcePermission"].content
    assert (by_symbol["isAdmin"].start_line, by_symbol["isAdmin"].end_line) == (15, 15)
    assert by_symbol["isAdmin"].content.endswith('user.role == "ADMIN"')


def test_kotlin_objects_interfaces_top_level_and_extension_functions():
    _, chunks = _fixture_chunks()
    by_symbol = {chunk.symbol: chunk for chunk in chunks}

    assert by_symbol["SessionManager"].chunk_type == "object"
    assert by_symbol["authenticatedUserId"].class_name == "SessionManager"
    assert by_symbol["UserGate"].chunk_type == "interface"
    assert by_symbol["isAllowed"].class_name == "UserGate"
    assert by_symbol["checkPermission"].class_name is None
    assert by_symbol["canDelete"].class_name is None
    assert "User.canDelete" in by_symbol["canDelete"].content


def test_kotlin_chunks_reuse_generic_security_tags():
    _, chunks = _fixture_chunks()
    enforce = next(chunk for chunk in chunks if chunk.symbol == "enforcePermission")

    assert "potential_access_check" in enforce.tags
    assert "potential_entry_point" in enforce.tags


def test_kotlin_nested_type_uses_immediate_enclosing_type():
    from app.services.parser import chunk_source

    code = """class Outer {
    class Inner {
        fun checkAccess() {
            println("checked")
        }
    }
}
"""
    chunk = next(item for item in chunk_source("Nested.kt", "kotlin", code) if item.symbol == "checkAccess")

    assert chunk.class_name == "Inner"
    assert (chunk.start_line, chunk.end_line) == (3, 5)


def test_kotlin_malformed_source_remains_indexable_with_lines():
    from app.services.parser import chunk_source

    chunks = chunk_source("Broken.kt", "kotlin", "class Broken {\n  fun incomplete( {\n")

    assert chunks
    assert all(chunk.start_line >= 1 and chunk.end_line >= chunk.start_line for chunk in chunks)


def test_kotlin_grammar_failure_uses_existing_line_fallback(monkeypatch):
    from app.services import parser

    monkeypatch.setattr(parser, "_chunk_kotlin_tree_sitter", lambda *args: [])
    source = "fun checkPermission() = true\n" * 90
    chunks = parser.chunk_source("Fallback.kt", "kotlin", source)

    assert chunks
    assert all(chunk.chunk_type == "line_range_fallback" for chunk in chunks)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 80), (81, 90)]


def test_kotlin_project_index_integration_persists_symbols_and_navigation(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    indexed_payloads = []
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: indexed_payloads.append(chunk.copy()) or f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="Kotlin fixture", source_type="github"))
    repository = Path(project["local_path"])
    target = repository / "src" / "AccessManager.kt"
    target.parent.mkdir(parents=True)
    target.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    project_service.index_project(project["id"])
    with db() as connection:
        file_row = connection.execute("SELECT * FROM files WHERE project_id = ?", (project["id"],)).fetchone()
        rows = connection.execute(
            "SELECT chunk_type, symbol_name, class_name, start_line, end_line FROM code_chunks WHERE project_id = ?",
            (project["id"],),
        ).fetchall()

    symbols = {row["symbol_name"] for row in rows}
    can_modify = next(row for row in rows if row["symbol_name"] == "canModify")
    assert file_row["file_path"] == "src/AccessManager.kt"
    assert file_row["language"] == "kotlin"
    assert {"canModify", "enforcePermission", "checkPermission"}.issubset(symbols)
    assert (can_modify["class_name"], can_modify["start_line"], can_modify["end_line"]) == ("AccessManager", 4, 6)
    assert any(item["language"] == "kotlin" and item["symbol_name"] == "canModify" for item in indexed_payloads)
    opened = project_service.file_content(project["id"], "src/AccessManager.kt")
    assert opened["language"] == "kotlin"
    assert "fun canModify" in opened["content"]


def test_mixed_java_kotlin_project_uses_both_structural_parsers(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(name="Mixed fixture", source_type="github"))
    repository = Path(project["local_path"])
    repository.joinpath("JavaAccessService.java").write_text(
        "public class JavaAccessService {\n"
        "  public void checkAccess() {\n"
        "    checkPermission();\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    repository.joinpath("KotlinAccessService.kt").write_text(
        "class KotlinAccessService {\n"
        "    fun enforceAccess() {\n"
        "        throw SecurityException(\"denied\")\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    project_service.index_project(project["id"])
    with db() as connection:
        rows = connection.execute(
            "SELECT f.file_path, f.language, c.chunk_type, c.symbol_name, c.class_name, c.start_line, c.end_line "
            "FROM code_chunks c JOIN files f ON f.id = c.file_id WHERE c.project_id = ?",
            (project["id"],),
        ).fetchall()

    java = next(row for row in rows if row["symbol_name"] == "checkAccess")
    kotlin = next(row for row in rows if row["symbol_name"] == "enforceAccess")
    assert (java["file_path"], java["language"], java["chunk_type"], java["class_name"], java["start_line"], java["end_line"]) == (
        "JavaAccessService.java", "java", "method", "JavaAccessService", 2, 4
    )
    assert (kotlin["file_path"], kotlin["language"], kotlin["chunk_type"], kotlin["class_name"], kotlin["start_line"], kotlin["end_line"]) == (
        "KotlinAccessService.kt", "kotlin", "function", "KotlinAccessService", 2, 4
    )
