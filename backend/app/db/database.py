import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    repo_url TEXT,
    local_path TEXT NOT NULL,
    subfolder_path TEXT,
    commit_hash TEXT,
    status TEXT NOT NULL,
    status_message TEXT,
    progress_percent INTEGER,
    files_indexed INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0,
    chunks_indexed INTEGER DEFAULT 0,
    total_chunks INTEGER DEFAULT 0,
    current_file TEXT,
    security_goal TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT,
    size_bytes INTEGER,
    line_count INTEGER,
    is_indexed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    symbol_name TEXT,
    class_name TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    code TEXT NOT NULL,
    security_tags TEXT,
    embedding_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    module_id TEXT,
    title TEXT NOT NULL,
    slug TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    wiki_schema_version TEXT DEFAULT '1.0',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    module_id TEXT,
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    evidence_json TEXT,
    raw_model_response TEXT,
    parsed_answer_json TEXT,
    validation_status TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    module_path TEXT,
    question TEXT,
    chat_message_id TEXT,
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    answer_text TEXT,
    parsed_answer_json TEXT,
    evidence_json TEXT,
    wiki_context_json TEXT,
    validation_status TEXT,
    correct_file_path INTEGER,
    correct_code_block INTEGER,
    explanation_quality INTEGER,
    completeness INTEGER,
    usefulness INTEGER,
    evaluator_comment TEXT,
    correctness_score INTEGER,
    evidence_quality_score INTEGER,
    score_file_path INTEGER,
    score_code_block INTEGER,
    score_explanation INTEGER,
    score_completeness INTEGER,
    hallucination_flag INTEGER,
    latency_ms INTEGER,
    estimated_cost REAL,
    human_comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    human_comment TEXT,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    settings = get_settings()
    Path(settings.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.sqlite_db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.executescript(SCHEMA)
        _ensure_column(connection, "chat_messages", "raw_model_response", "TEXT")
        _ensure_column(connection, "wiki_pages", "wiki_schema_version", "TEXT DEFAULT '1.0'")
        _ensure_column(connection, "projects", "subfolder_path", "TEXT")
        _ensure_column(connection, "projects", "progress_percent", "INTEGER")
        _ensure_column(connection, "projects", "files_indexed", "INTEGER DEFAULT 0")
        _ensure_column(connection, "projects", "total_files", "INTEGER DEFAULT 0")
        _ensure_column(connection, "projects", "chunks_indexed", "INTEGER DEFAULT 0")
        _ensure_column(connection, "projects", "total_chunks", "INTEGER DEFAULT 0")
        _ensure_column(connection, "projects", "current_file", "TEXT")
        _ensure_column(connection, "chat_messages", "parsed_answer_json", "TEXT")
        _ensure_column(connection, "chat_messages", "validation_status", "TEXT")
        _ensure_column(connection, "evaluations", "project_id", "TEXT")
        _ensure_column(connection, "evaluations", "module_path", "TEXT")
        _ensure_column(connection, "evaluations", "question", "TEXT")
        _ensure_column(connection, "evaluations", "answer_text", "TEXT")
        _ensure_column(connection, "evaluations", "parsed_answer_json", "TEXT")
        _ensure_column(connection, "evaluations", "evidence_json", "TEXT")
        _ensure_column(connection, "evaluations", "wiki_context_json", "TEXT")
        _ensure_column(connection, "evaluations", "validation_status", "TEXT")
        _ensure_column(connection, "evaluations", "correct_file_path", "INTEGER")
        _ensure_column(connection, "evaluations", "correct_code_block", "INTEGER")
        _ensure_column(connection, "evaluations", "explanation_quality", "INTEGER")
        _ensure_column(connection, "evaluations", "completeness", "INTEGER")
        _ensure_column(connection, "evaluations", "usefulness", "INTEGER")
        _ensure_column(connection, "evaluations", "evaluator_comment", "TEXT")
        _ensure_column(connection, "evaluations", "correctness_score", "INTEGER")
        _ensure_column(connection, "evaluations", "evidence_quality_score", "INTEGER")
        _ensure_column(connection, "evaluations", "hallucination_flag", "INTEGER")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
