import asyncio
import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _local_android_repository(root: Path) -> tuple[Path, str]:
    repository = root / "android-source"
    source = repository / "services" / "accounts"
    ignored = repository / "other"
    source.mkdir(parents=True)
    ignored.mkdir()
    (source / "AccountManagerService.java").write_text(
        "public class AccountManagerService {\n"
        "  public void getToken() { checkPermission(); }\n"
        "}\n",
        encoding="utf-8",
    )
    (ignored / "Ignored.java").write_text("public class Ignored {}\n", encoding="utf-8")
    _git("init", cwd=repository)
    _git("config", "user.email", "android-test@example.invalid", cwd=repository)
    _git("config", "user.name", "Android Import Test", cwd=repository)
    _git("add", ".", cwd=repository)
    _git("commit", "-m", "fixture", cwd=repository)
    revision = _git("rev-parse", "HEAD", cwd=repository)
    bare_repository = root / "android-source.git"
    subprocess.run(
        ["git", "clone", "--bare", repository.as_posix(), bare_repository.as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    return bare_repository, revision


def test_android_git_import_persists_metadata_clones_revision_and_indexes_subfolder(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    repository, revision = _local_android_repository(isolated_env)
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    repository_url = repository.as_posix()
    project = project_service.create_project(ProjectCreate(
        name="Account study",
        source_type="android",
        android_source_url=repository_url,
        android_case_study="account-manager-service",
        subfolder_path="services/accounts",
        security_goal="Understand account token access control",
    ))

    project_service.import_and_index_project(project["id"])
    stored = project_service.get_project(project["id"])
    with db() as connection:
        files = [row["file_path"] for row in connection.execute(
            "SELECT file_path FROM files WHERE project_id = ? ORDER BY file_path", (project["id"],)
        )]
        chunk_count = connection.execute(
            "SELECT COUNT(*) FROM code_chunks WHERE project_id = ?", (project["id"],)
        ).fetchone()[0]

    assert stored["source_type"] == "android"
    assert stored["repo_url"] == repository_url
    assert stored["android_case_study"] == "account-manager-service"
    assert stored["subfolder_path"] == "services/accounts"
    assert stored["security_goal"] == "Understand account token access control"
    assert stored["commit_hash"] == revision
    assert stored["status"] == "indexed"
    assert files == ["services/accounts/AccountManagerService.java"]
    assert chunk_count > 0
    assert project_service.file_content(project["id"], files[0])["language"] == "java"


def test_custom_android_project_has_no_case_study(isolated_env):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    repository, _ = _local_android_repository(isolated_env)
    project = project_service.create_project(ProjectCreate(
        name="Custom Android",
        source_type="android",
        android_source_url=repository.as_posix(),
    ))
    assert project["android_case_study"] is None


def test_android_project_requires_repository_url(isolated_env):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    with pytest.raises(ValueError, match="Git-cloneable Android repository URL is required"):
        project_service.create_project(ProjectCreate(name="Missing source", source_type="android"))


def test_invalid_android_git_source_marks_import_failed(isolated_env):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    project = project_service.create_project(ProjectCreate(
        name="Invalid source",
        source_type="android",
        android_source_url=str(isolated_env / "not-a-repository"),
    ))
    project_service.import_and_index_project(project["id"])
    stored = project_service.get_project(project["id"])
    assert stored["status"] == "failed"
    assert "Git clone failed" in stored["status_message"]


def test_android_invalid_subfolder_uses_shared_failure_behavior(isolated_env, monkeypatch):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    repository, _ = _local_android_repository(isolated_env)
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    project = project_service.create_project(ProjectCreate(
        name="Missing scope",
        source_type="android",
        android_source_url=repository.as_posix(),
        subfolder_path="missing/path",
    ))
    project_service.import_and_index_project(project["id"])
    stored = project_service.get_project(project["id"])
    assert stored["status"] == "failed"
    assert "Selected subfolder does not exist" in stored["status_message"]


def test_github_project_remains_without_android_case_study(isolated_env):
    from app.db.database import init_db
    from app.db.schemas import ProjectCreate
    from app.services import project_service

    init_db()
    project = project_service.create_project(ProjectCreate(
        name="Normal GitHub",
        source_type="github",
        repo_url="https://github.com/example/repository.git",
        android_case_study="account-manager-service",
    ))
    assert project["android_case_study"] is None


def test_generic_zip_import_accepts_android_like_java_source(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    monkeypatch.setattr(project_service, "index_code_chunk", lambda chunk: f"code:{chunk['id']}")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "android/app/src/main/java/example/MainActivity.java",
            "public class MainActivity { public void onCreate() {} }\n",
        )
    payload.seek(0)

    project = asyncio.run(project_service.create_project_from_zip(
        "Android ZIP", UploadFile(filename="android-source.zip", file=payload), "Inspect activity access control"
    ))
    with db() as connection:
        files = [row["file_path"] for row in connection.execute(
            "SELECT file_path FROM files WHERE project_id = ?", (project["id"],)
        )]

    assert project["source_type"] == "zip"
    assert project["android_case_study"] is None
    assert project["status"] == "indexed"
    assert files == ["android/app/src/main/java/example/MainActivity.java"]
