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

CREATE TABLE IF NOT EXISTS formal_runs (
    run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation TEXT NOT NULL,
    question TEXT, timestamp TEXT NOT NULL, provider_model_json TEXT, answer_json TEXT,
    primary_evidence_json TEXT, wiki_context_json TEXT, execution_status TEXT,
    comparison_metadata_json TEXT, evaluation_config_hash TEXT,
    human_evaluation_id TEXT, human_evaluation_status TEXT
);

CREATE TABLE IF NOT EXISTS model_usage (
    execution_id TEXT PRIMARY KEY, run_id TEXT, project_id TEXT, operation TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL,
    provider_reported_input_tokens INTEGER, provider_reported_output_tokens INTEGER,
    provider_reported_total_tokens INTEGER, provider_reported_cached_input_tokens INTEGER,
    provider_reported_reasoning_tokens INTEGER, provider_reported_thinking_tokens INTEGER,
    usage_source TEXT NOT NULL, request_duration_ms INTEGER, load_duration_ms REAL,
    prompt_eval_duration_ms REAL, generation_duration_ms REAL,
    api_cost REAL, compute_energy_cost REAL, pricing_revision TEXT,
    native_usage_json TEXT, prompt_composition_json TEXT,
    supplied_source_chunk_ids_json TEXT, cited_source_chunk_ids_json TEXT,
    supplied_wiki_chunk_ids_json TEXT, supplied_source_package_hash TEXT,
    supplied_wiki_package_hash TEXT, status TEXT, warnings_json TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_pricing (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, effective_from TEXT NOT NULL,
    currency TEXT NOT NULL, input_price_per_million REAL, cached_input_price_per_million REAL,
    output_price_per_million REAL, reasoning_price_per_million REAL,
    pricing_source TEXT, pricing_revision TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_baselines (
    name TEXT PRIMARY KEY, project_id TEXT NOT NULL, phase TEXT NOT NULL,
    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runtime_metadata (
    provider TEXT NOT NULL, model TEXT NOT NULL, digest TEXT, metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL, captured_at TEXT NOT NULL,
    PRIMARY KEY (provider, model)
);

CREATE TABLE IF NOT EXISTS scenario_pricing (
    revision TEXT PRIMARY KEY, model TEXT NOT NULL, currency TEXT NOT NULL,
    uncached_input_price_per_million REAL NOT NULL,
    cached_input_price_per_million REAL,
    output_price_per_million REAL NOT NULL,
    effective_date TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
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
        _ensure_column(connection, "evaluations", "evidence_discipline_score", "INTEGER")
        _ensure_column(connection, "evaluations", "source_reference_accuracy_score", "INTEGER")
        _ensure_column(connection, "evaluations", "verdict", "TEXT")
        _ensure_column(connection, "evaluations", "evaluation_config_hash", "TEXT")
        _ensure_column(connection, "evaluations", "evaluation_type", "TEXT DEFAULT 'model'")
        _ensure_column(connection, "formal_runs", "supplied_source_evidence_json", "TEXT")
        _ensure_column(connection, "formal_runs", "cited_source_evidence_json", "TEXT")
        _ensure_column(connection, "formal_runs", "supplied_source_package_hash", "TEXT")
        _ensure_column(connection, "formal_runs", "supplied_wiki_package_hash", "TEXT")
        _ensure_column(connection, "formal_runs", "evaluation_config_json", "TEXT")
        _ensure_column(connection, "model_usage", "model_configuration_json", "TEXT")
        _ensure_column(connection, "formal_runs", "run_purpose", "TEXT DEFAULT 'development'")
        _ensure_column(connection, "formal_runs", "question_id", "TEXT")
        _ensure_column(connection, "model_usage", "run_purpose", "TEXT DEFAULT 'development'")
        _ensure_column(connection, "model_usage", "provider_queue_duration_ms", "REAL")
        _ensure_column(connection, "model_usage", "provider_total_duration_ms", "REAL")
        connection.execute("""INSERT OR IGNORE INTO scenario_pricing
            (revision,model,currency,uncached_input_price_per_million,cached_input_price_per_million,output_price_per_million,effective_date,source,created_at)
            VALUES ('gpt-4o-mini-2024-07-18-v1','gpt-4o-mini','USD',0.15,0.075,0.60,'2024-07-18','https://platform.openai.com/docs/models/gpt-4o-mini','2026-08-17T00:00:00+00:00')""")
        connection.execute("""INSERT OR IGNORE INTO model_pricing
            (id,provider,model,effective_from,currency,input_price_per_million,cached_input_price_per_million,output_price_per_million,reasoning_price_per_million,pricing_source,pricing_revision,created_at)
            VALUES ('groq-openai-gpt-oss-20b-2025-08-05-v1','groq','openai/gpt-oss-20b','2025-08-05','USD',0.075,NULL,0.30,NULL,'https://console.groq.com/docs/models','groq-gpt-oss-20b-2025-08-05-v1','2026-08-17T00:00:00+00:00')""")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
