import csv
import io
import json
import shutil
import uuid
from pathlib import Path


def _client_and_headers(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core.security import create_access_token
    from app.db.database import init_db
    from app.services import vector_index

    temp_root = Path(__file__).resolve().parents[1] / ".export_eval_tmp" / str(uuid.uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SQLITE_DB_PATH", str(temp_root / "security_codewiki.db"))
    monkeypatch.setenv("PROJECT_STORAGE_PATH", str(temp_root / "projects"))
    monkeypatch.setenv("CHROMA_DB_PATH", str(temp_root / "chroma"))
    monkeypatch.setenv("EXPORT_STORAGE_PATH", str(temp_root / "exports"))
    monkeypatch.setenv("APP_SUPERUSER_USERNAME", "admin")
    monkeypatch.setenv("APP_SUPERUSER_PASSWORD", "change_me")
    monkeypatch.setenv("APP_SECRET_KEY", "test_secret")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    get_settings.cache_clear()
    vector_index._embedding_provider = None

    init_db()
    from app.main import app

    settings = get_settings()
    token = create_access_token(settings.app_superuser_username, settings)
    return TestClient(app), {"Authorization": f"Bearer {token}"}, temp_root


def _seed_export_project() -> tuple[str, str]:
    from app.db.database import db
    from app.services.project_service import now

    project_id = "export-project-1"
    evaluation_id = "evaluation-1"
    timestamp = now()
    evidence = [
        {
            "chunk_id": "chunk-1",
            "file_path": "src/AuthService.java",
            "symbol_name": "checkPermission",
            "start_line": 10,
            "end_line": 18,
            "code_snippet": "checkPermission();",
        }
    ]
    with db() as connection:
        connection.execute(
            """
            INSERT INTO projects
            (id, name, source_type, repo_url, local_path, status, status_message, files_indexed, chunks_indexed, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, "Export Demo", "zip", None, "C:/tmp/export-demo/repo", "indexed", "Ready.", 1, 1, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO evaluations
            (id, project_id, module_path, question, model_provider, model_name, answer_text, evidence_json, validation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                project_id,
                "src/AuthService.java",
                "Where is permission checked?",
                "ollama",
                "local-model",
                "Permission is checked in checkPermission.",
                json.dumps(evidence),
                "valid_json",
                timestamp,
            ),
        )
    return project_id, evaluation_id


def test_export_endpoint_returns_markdown_content_disposition(monkeypatch):
    client, headers, temp_root = _client_and_headers(monkeypatch)
    project_id, _ = _seed_export_project()

    response = client.get(f"/api/projects/{project_id}/export?format=markdown", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="Export_Demo_audit_report.md"')
    assert "# Security Audit Report" in response.text
    assert "## 1. Project Information" in response.text
    shutil.rmtree(temp_root, ignore_errors=True)


def test_export_endpoint_returns_model_comparison_csv_headers(monkeypatch):
    client, headers, temp_root = _client_and_headers(monkeypatch)
    project_id, _ = _seed_export_project()

    response = client.get(f"/api/projects/{project_id}/export?format=csv", headers=headers)

    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "question_id",
        "question_text",
        "model_provider",
        "model_name",
        "evidence_count",
        "answer_length_chars",
        "human_correctness_score",
        "human_evidence_quality_score",
        "hallucination_flag",
        "notes",
    ]
    shutil.rmtree(temp_root, ignore_errors=True)


def test_evaluation_scoring_accepts_thesis_rubric_fields(monkeypatch):
    client, headers, temp_root = _client_and_headers(monkeypatch)
    project_id, evaluation_id = _seed_export_project()

    response = client.patch(
        f"/api/projects/{project_id}/evaluations/{evaluation_id}",
        headers=headers,
        json={"correctness": 2, "evidence_quality": 1, "hallucination": False, "notes": "Manual evaluation feedback."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["correctness_score"] == 2
    assert payload["evidence_quality_score"] == 1
    assert payload["hallucination_flag"] == 0
    assert payload["evaluator_comment"] == "Manual evaluation feedback."
    shutil.rmtree(temp_root, ignore_errors=True)


def test_evaluation_scoring_rejects_out_of_range_correctness(monkeypatch):
    client, headers, temp_root = _client_and_headers(monkeypatch)
    project_id, evaluation_id = _seed_export_project()

    response = client.patch(
        f"/api/projects/{project_id}/evaluations/{evaluation_id}",
        headers=headers,
        json={"correctness": 3, "evidence_quality": 1, "hallucination": False},
    )

    assert response.status_code == 422
    shutil.rmtree(temp_root, ignore_errors=True)
