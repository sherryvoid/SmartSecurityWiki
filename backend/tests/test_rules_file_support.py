from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rules_security_sample" / "access.rules"


def test_rules_files_are_discovered_and_classified_as_generic_text():
    from app.services.files import is_relevant_file, language_for_path

    assert is_relevant_file(FIXTURE_PATH)
    assert language_for_path("firebase/access.rules") == "text"


def test_rules_files_use_line_aware_generic_fallback_with_exact_source():
    from app.services.parser import chunk_source

    source = FIXTURE_PATH.read_text(encoding="utf-8")
    chunks = chunk_source("firebase/access.rules", "text", source)

    assert len(chunks) == 1
    assert chunks[0].chunk_type == "line_range_fallback"
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 18)
    assert chunks[0].content == source.rstrip("\n")
    assert "potential_access_check" in chunks[0].tags


def test_rules_files_persist_index_and_open_through_source_navigation(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    indexed_payloads = []
    monkeypatch.setattr(
        project_service,
        "index_code_chunk",
        lambda chunk: indexed_payloads.append(chunk.copy()) or f"code:{chunk['id']}",
    )
    project = project_service.create_project(ProjectCreate(name="Rules fixture", source_type="github"))
    repository = Path(project["local_path"])
    target = repository / "firebase" / "access.rules"
    target.parent.mkdir(parents=True)
    source = FIXTURE_PATH.read_text(encoding="utf-8")
    target.write_text(source, encoding="utf-8")

    project_service.index_project(project["id"])

    with db() as connection:
        file_row = connection.execute(
            "SELECT file_path, language, is_indexed FROM files WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
        chunk_row = connection.execute(
            "SELECT chunk_type, start_line, end_line, code, embedding_id FROM code_chunks WHERE project_id = ?",
            (project["id"],),
        ).fetchone()

    assert tuple(file_row) == ("firebase/access.rules", "text", 1)
    assert chunk_row["chunk_type"] == "line_range_fallback"
    assert (chunk_row["start_line"], chunk_row["end_line"]) == (1, 18)
    assert chunk_row["code"] == source.rstrip("\n")
    assert chunk_row["embedding_id"] == f"code:{indexed_payloads[0]['id']}"
    assert indexed_payloads[0]["file_path"] == "firebase/access.rules"
    assert indexed_payloads[0]["language"] == "text"

    opened = project_service.file_content(project["id"], "firebase/access.rules")
    assert opened == {
        "path": "firebase/access.rules",
        "content": source,
        "language": "text",
    }
