# READ SUMMARY: This conftest prepares backend import paths and isolated environment fixtures for pytest.
# CHANGED: Added a Windows-safe tmp_path fixture that avoids locked pytest-managed temp roots.
import shutil
import sys
import uuid
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture()
def tmp_path():
    root = BACKEND_ROOT / "manual-test-runtime"
    root.mkdir(parents=True, exist_ok=True)
    path = root / str(uuid.uuid4())
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import vector_index

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "security_codewiki.db"))
    monkeypatch.setenv("PROJECT_STORAGE_PATH", str(tmp_path / "projects"))
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setenv("EXPORT_STORAGE_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("APP_SUPERUSER_USERNAME", "admin")
    monkeypatch.setenv("APP_SUPERUSER_PASSWORD", "change_me")
    monkeypatch.setenv("APP_SECRET_KEY", "test_secret")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    get_settings.cache_clear()
    vector_index._embedding_provider = None
    yield tmp_path
    get_settings.cache_clear()
    vector_index._embedding_provider = None


@pytest.fixture
def ephemeral_chroma():
    import chromadb

    client = chromadb.Client()
    yield client
