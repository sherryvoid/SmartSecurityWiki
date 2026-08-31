# READ SUMMARY: This module creates, imports, indexes, deletes, and retrieves project evidence from SQLite and ChromaDB.
# CHANGED: Added selected-file retrieval preference while keeping helper/security files retrievable.
import os
import json
import re
import shutil
import sqlite3
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.db.database import db
from app.db.schemas import ProjectCreate
from app.services.files import build_file_tree, is_relevant_file, language_for_path, read_text, safe_relative_path
from app.services.parser import chunk_source
from app.services.security_detection import confidence_for_tags, detect_security_tags
from app.services.vector_index import HTTP_VERB_TAGS, clear_project, expand_security_query, has_manifest_component_intent, index_code_chunk, query as vector_query, rescore_chunks


SELECTED_FILE_BOOST = 0.18


ANDROID_CASE_STUDIES = [
    {"id": "account-manager-service", "name": "AccountManagerService", "hint": "Use a Git-cloneable Android repository containing AccountManagerService.java.", "repository_url": None, "revision": None, "subfolder_path": None, "default_security_goal": None},
    {"id": "service-manager", "name": "ServiceManager", "hint": "Use a Git-cloneable repository containing Android framework/native service manager sources.", "repository_url": None, "revision": None, "subfolder_path": None, "default_security_goal": None},
    {"id": "binder-token-handling", "name": "Binder Token Handling", "hint": "Use a Git-cloneable repository containing the Android Binder sources to study.", "repository_url": None, "revision": None, "subfolder_path": None, "default_security_goal": None},
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row) -> dict:
    return dict(row) if row else {}


def create_project(payload: ProjectCreate) -> dict:
    settings = get_settings()
    project_id = str(uuid.uuid4())
    project_root = Path(settings.project_storage_path) / project_id
    repo_path = project_root / "repo"
    (project_root / "metadata").mkdir(parents=True, exist_ok=True)
    (project_root / "wiki").mkdir(parents=True, exist_ok=True)
    repo_path.mkdir(parents=True, exist_ok=True)
    source_url = normalize_source_url(payload.repo_url if payload.source_type == "github" else payload.android_source_url)
    if payload.source_type == "android" and not source_url:
        raise ValueError("A Git-cloneable Android repository URL is required.")
    subfolder_path = normalize_subfolder_path(payload.subfolder_path)
    android_case_study = payload.android_case_study.strip() if payload.source_type == "android" and payload.android_case_study and payload.android_case_study.strip() else None
    timestamp = now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO projects (id, name, source_type, repo_url, local_path, subfolder_path, android_case_study, commit_hash, status, status_message, security_goal, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.name,
                payload.source_type,
                source_url,
                str(repo_path.resolve()),
                subfolder_path,
                android_case_study,
                None,
                "created",
                "Project created.",
                payload.security_goal,
                timestamp,
                timestamp,
            ),
        )
    return get_project(project_id)


async def create_project_from_zip(name: str, upload: UploadFile, security_goal: str | None = None) -> dict:
    project = create_project(ProjectCreate(name=name, source_type="zip", security_goal=security_goal))
    repo_path = Path(project["local_path"]).resolve()
    archive_path = repo_path.parent / "metadata" / "upload.zip"
    with archive_path.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)
    update_status(project["id"], "fetching", "Extracting ZIP archive...")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract_zip(archive, repo_path)
        update_status(project["id"], "fetched", "ZIP extracted.")
    except zipfile.BadZipFile as exc:
        update_status(project["id"], "failed", f"Invalid ZIP file: {exc}")
        return get_project(project["id"])
    index_project(project["id"])
    return get_project(project["id"])


def import_and_index_project(project_id: str) -> None:
    project = get_project(project_id)
    if not project:
        return
    try:
        update_status(project_id, "fetching", "Cloning repository... progress percentage is unavailable until clone completes.", progress_percent=None)
        if project["source_type"] in {"github", "android"} and project["repo_url"]:
            _clone_repository(project["repo_url"], Path(project["local_path"]).resolve())
            commit_hash = _commit_hash(Path(project["local_path"]).resolve())
            with db() as connection:
                connection.execute("UPDATE projects SET commit_hash = ? WHERE id = ?", (commit_hash, project_id))
        update_status(project_id, "fetched", "Repository fetched.")
        index_project(project_id)
    except Exception as exc:
        update_status(project_id, "failed", f"Import failed: {exc}")


def index_project(project_id: str) -> None:
    settings = get_settings()
    project = get_project(project_id)
    if not project:
        return
    repo_root = Path(project["local_path"]).resolve()
    index_root = project_index_root(project)
    if not index_root.exists() or not index_root.is_dir():
        update_status(project_id, "failed", f"Selected subfolder does not exist: {project.get('subfolder_path')}")
        return
    update_status(project_id, "scanning", "Scanning files for supported source types...", progress_percent=5, current_file=None)
    with db() as connection:
        connection.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM code_chunks WHERE project_id = ?", (project_id,))
    clear_project(project_id)
    repo_size_mb = _directory_size_mb(index_root)
    if settings.max_repo_size_mb > 0 and repo_size_mb > settings.max_repo_size_mb:
        update_status(
            project_id,
            "failed",
            f"Repository is {repo_size_mb:.1f} MB, above MAX_REPO_SIZE_MB={settings.max_repo_size_mb}. Use a smaller ZIP/subfolder for this MVP.",
        )
        return
    candidate_files = [path for path in index_root.rglob("*") if path.is_file() and is_relevant_file(path)]
    total_files = min(len(candidate_files), settings.max_total_files_to_index)
    update_status(project_id, "indexing_files", f"Found {len(candidate_files)} supported files. Preparing to index...", progress_percent=10, files_indexed=0, total_files=total_files, chunks_indexed=0, total_chunks=0)
    indexed_files = 0
    indexed_chunks = 0
    total_chunks_seen = 0
    limit_warning = ""
    for file_path in candidate_files:
        if indexed_files >= settings.max_total_files_to_index:
            limit_warning = f"Partial index: reached MAX_TOTAL_FILES_TO_INDEX={settings.max_total_files_to_index}."
            break
        relative = file_path.relative_to(repo_root).as_posix()
        text = read_text(file_path)
        lines = text.splitlines()
        file_id = str(uuid.uuid4())
        language = language_for_path(relative)
        with db() as connection:
            connection.execute(
                """
                INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, project_id, relative, language, file_path.stat().st_size, len(lines), 0, now()),
            )
        file_progress = 10 + int((indexed_files / max(total_files, 1)) * 45)
        update_status(project_id, "indexing_files", f"Extracting symbols from {relative}", progress_percent=file_progress, files_indexed=indexed_files, total_files=total_files, chunks_indexed=indexed_chunks, total_chunks=total_chunks_seen, current_file=relative)
        chunks = chunk_source(relative, language, text)
        total_chunks_seen += len(chunks)
        remaining_chunks = settings.max_total_chunks_to_index - indexed_chunks
        if remaining_chunks <= 0:
            limit_warning = f"Partial index: reached MAX_TOTAL_CHUNKS_TO_INDEX={settings.max_total_chunks_to_index}."
            break
        if len(chunks) > remaining_chunks:
            chunks = chunks[:remaining_chunks]
            limit_warning = f"Partial index: reached MAX_TOTAL_CHUNKS_TO_INDEX={settings.max_total_chunks_to_index}."
        embedding_progress = 55 + int((indexed_files / max(total_files, 1)) * 40)
        update_status(project_id, "embedding", f"Embedding chunks from {relative}", progress_percent=embedding_progress, files_indexed=indexed_files, total_files=total_files, chunks_indexed=indexed_chunks, total_chunks=total_chunks_seen, current_file=relative)
        with db() as connection:
            for chunk in chunks:
                chunk_id = str(uuid.uuid4())
                chunk_record = {
                    "id": chunk_id,
                    "project_id": project_id,
                    "file_id": file_id,
                    "file_path": relative,
                    "language": language,
                    "chunk_type": chunk.chunk_type,
                    "symbol_name": chunk.symbol_name,
                    "class_name": chunk.class_name,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "code": chunk.code,
                    "security_tags": ",".join(chunk.security_tags),
                    "http_method": chunk.http_method,
                }
                embedding_id = index_code_chunk(chunk_record)
                connection.execute(
                    """
                    INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        project_id,
                        file_id,
                        chunk.chunk_type,
                        chunk.symbol_name,
                        chunk.class_name,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.code,
                        chunk_record["security_tags"],
                        embedding_id,
                        now(),
                    ),
                )
                indexed_chunks += 1
            connection.execute("UPDATE files SET is_indexed = 1 WHERE id = ?", (file_id,))
        indexed_files += 1
        update_status(project_id, "indexing_chunks", f"Indexed {relative}", progress_percent=55 + int((indexed_files / max(total_files, 1)) * 40), files_indexed=indexed_files, total_files=total_files, chunks_indexed=indexed_chunks, total_chunks=total_chunks_seen, current_file=relative)
        if limit_warning:
            break
    final_message = limit_warning or "Ready."
    if limit_warning:
        final_message += f" Indexed {indexed_files} files and {indexed_chunks} chunks."
    update_status(project_id, "indexed", final_message, progress_percent=100, files_indexed=indexed_files, total_files=total_files, chunks_indexed=indexed_chunks, total_chunks=total_chunks_seen, current_file=None)


def list_projects() -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [row_to_dict(row) for row in rows]


def delete_project(project_id: str) -> dict:
    settings = get_settings()
    project = get_project(project_id)
    if not project:
        return {"deleted": False, "project_id": project_id, "message": "Project not found."}

    with db() as connection:
        chat_session_rows = connection.execute("SELECT id FROM chat_sessions WHERE project_id = ?", (project_id,)).fetchall()
        chat_session_ids = [row["id"] for row in chat_session_rows]
        chunk_rows = connection.execute("SELECT id FROM code_chunks WHERE project_id = ?", (project_id,)).fetchall()
        wiki_rows = connection.execute("SELECT id FROM wiki_pages WHERE project_id = ?", (project_id,)).fetchall()
        message_ids: list[str] = []
        if chat_session_ids:
            placeholders = ",".join("?" for _ in chat_session_ids)
            message_rows = connection.execute(f"SELECT id FROM chat_messages WHERE session_id IN ({placeholders})", chat_session_ids).fetchall()
            message_ids = [row["id"] for row in message_rows]

        verification_targets = [row["id"] for row in chunk_rows] + [row["id"] for row in wiki_rows] + message_ids
        if verification_targets:
            placeholders = ",".join("?" for _ in verification_targets)
            connection.execute(f"DELETE FROM verifications WHERE target_id IN ({placeholders})", verification_targets)
        if chat_session_ids:
            placeholders = ",".join("?" for _ in chat_session_ids)
            connection.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", chat_session_ids)
        connection.execute("DELETE FROM chat_sessions WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM evaluations WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM wiki_pages WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM code_chunks WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    clear_project(project_id)
    storage_root = Path(settings.project_storage_path).resolve()
    local_path = Path(project["local_path"]).resolve()
    project_root = local_path.parent
    if project_root == storage_root or storage_root not in project_root.parents:
        return {"deleted": True, "project_id": project_id, "storage_deleted": False, "message": "Database and vector records deleted. Storage path was outside project storage root."}
    shutil.rmtree(project_root, ignore_errors=True)
    return {"deleted": True, "project_id": project_id, "storage_deleted": True, "message": "Project, indexed chunks, vectors, and files deleted."}


def get_project(project_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_to_dict(row)


def update_status(
    project_id: str,
    status: str,
    message: str,
    progress_percent: int | None = None,
    files_indexed: int | None = None,
    total_files: int | None = None,
    chunks_indexed: int | None = None,
    total_chunks: int | None = None,
    current_file: str | None = None,
) -> None:
    updates = ["status = ?", "status_message = ?", "updated_at = ?"]
    params: list[object] = [status, message, now()]
    optional_fields = {
        "progress_percent": progress_percent,
        "files_indexed": files_indexed,
        "total_files": total_files,
        "chunks_indexed": chunks_indexed,
        "total_chunks": total_chunks,
        "current_file": current_file,
    }
    for field, value in optional_fields.items():
        if value is not None or field in {"progress_percent", "current_file"}:
            updates.append(f"{field} = ?")
            params.append(value)
    params.append(project_id)
    with db() as connection:
        connection.execute(
            f"UPDATE projects SET {', '.join(updates)} WHERE id = ?",
            params,
        )


def project_status(project_id: str) -> dict:
    project = get_project(project_id)
    return {"status": project.get("status"), "status_message": project.get("status_message"), "project": project}


def file_tree(project_id: str) -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT file_path FROM files WHERE project_id = ? ORDER BY file_path", (project_id,)).fetchall()
    return build_file_tree([row["file_path"] for row in rows])


def file_content(project_id: str, path: str) -> dict:
    project = get_project(project_id)
    repo_root = Path(project["local_path"])
    candidate = safe_relative_path(repo_root, path)
    text = read_text(candidate)
    return {"path": path, "content": text, "language": language_for_path(path)}


def project_index_root(project: dict) -> Path:
    repo_root = Path(project["local_path"])
    subfolder_path = normalize_subfolder_path(project.get("subfolder_path"))
    if not subfolder_path:
        return repo_root
    return safe_relative_path(repo_root, subfolder_path)


def normalize_subfolder_path(subfolder_path: str | None) -> str | None:
    if not subfolder_path:
        return None
    cleaned = subfolder_path.replace("\\", "/").strip().strip("/")
    if not cleaned or cleaned == ".":
        return None
    candidate = Path(cleaned)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Subfolder path must be a safe relative path inside the repository.")
    return cleaned


def normalize_source_url(source_url: str | None) -> str | None:
    if source_url is None:
        return None
    cleaned = source_url.strip()
    return cleaned or None


QUERY_STOP_WORDS = {
    "the", "this", "that", "with", "from", "into", "where", "when", "which", "what",
    "how", "does", "such", "whether", "identify", "explain", "state", "support", "every",
    "relevant", "application", "repository", "source", "evidence", "code", "added", "created",
}


def _identifier_parts(value: str) -> set[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value.replace("_", " ").replace("-", " "))
    return {part.lower() for part in re.findall(r"[A-Za-z][A-Za-z0-9]*", separated) if len(part) > 2}


def _query_features(query: str) -> dict:
    raw_identifiers = re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:_[A-Za-z0-9_$]+|[A-Z][A-Za-z0-9_$]*)+\b", query)
    identifiers = list(dict.fromkeys(raw_identifiers))
    terms = {
        term.lower() for term in re.findall(r"[A-Za-z][A-Za-z0-9_]*", query)
        if len(term) > 2 and term.lower() not in QUERY_STOP_WORDS
    }
    for identifier in identifiers:
        terms.update(_identifier_parts(identifier))
    return {"terms": terms, "identifiers": identifiers}


def _query_match_signals(query: str, text: str, symbols: list[str]) -> dict:
    features = _query_features(query)
    lowered = text.lower()
    lexical_terms = sorted(term for term in features["terms"] if term in lowered)
    exact_identifiers = [identifier for identifier in features["identifiers"] if identifier.lower() in lowered]
    symbol_matches = []
    best_symbol = 0.0
    query_identifiers = [identifier.lower() for identifier in features["identifiers"]]
    for symbol in symbols:
        if not symbol:
            continue
        symbol_lower = symbol.lower()
        symbol_parts = _identifier_parts(symbol)
        score = 0.0
        for identifier in query_identifiers:
            if symbol_lower == identifier:
                score = max(score, 1.0)
            elif symbol_lower.startswith(identifier) or identifier.startswith(symbol_lower):
                score = max(score, 0.9)
            elif _identifier_parts(identifier) and _identifier_parts(identifier).issubset(symbol_parts):
                score = max(score, 0.75)
        if score:
            symbol_matches.append(symbol)
            best_symbol = max(best_symbol, score)
    return {
        "lexical_match_count": len(lexical_terms),
        "lexical_terms": lexical_terms,
        "lexical_relevance": min(1.0, len(lexical_terms) / max(1, min(8, len(features["terms"])))),
        "symbol_matches": symbol_matches,
        "symbol_relevance": best_symbol,
        "exact_identifiers": exact_identifiers,
        "exact_identifier_relevance": min(1.0, len(exact_identifiers) / max(1, len(features["identifiers"]))) if features["identifiers"] else 0.0,
    }


EXISTENCE_SUBJECTS = r"(?:(?:this|the)\s+)?(?:repository|project|application|codebase|indexed\s+source|source(?:\s+(?:tree|code))?|code)"
EXISTENCE_VERB_WORDS = r"uses?|defines?|declares?|assigns?|configures?|checks?|authorizes?|references?|contains?|includes?|implements?|provides?|supports?|finds?|has|have"
EXISTENCE_VERBS = rf"(?:{EXISTENCE_VERB_WORDS})"
EXISTENCE_STATES = r"(?:configured|defined|declared|assigned|checked|authorized|used|referenced|present|supported|implemented|found|absent|missing)"
EXPLICIT_CONCEPT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[A-Z][A-Z0-9]{2,})(?![A-Za-z0-9_])")


def _existence_intent(query: str) -> bool:
    clauses = re.split(r"(?<=[?.;])\s+", " ".join(query.replace("\n", " ").split()))
    relevant_clauses = [
        clause for clause in clauses
        if not re.match(r"^(?:do\s+not|don't|without|support\s+every)\b", clause.strip(), re.IGNORECASE)
    ]
    lowered = " ".join(relevant_clauses).lower()
    repository_scope = bool(re.search(r"\b(?:repository|project|application|codebase|indexed\s+source|source\s+code|source\s+tree|anywhere(?:\s+in\s+source)?)\b", lowered))
    existence_action = bool(re.search(r"\b(?:define|defined|declare|declared|assign|assigned|configure|configured|check|checked|authorize|authorized|use|used|reference|referenced|present|support|supported|implement|implemented|found|absent|missing|exist|exists)\b", lowered))
    grouped_implemented = bool(re.search(r"\bwhich\s+of\b.+?\b(?:implemented|present|found|defined|used)\b", lowered))
    named_concept = bool(re.search(r"\b(?:concept|identifier|name)\s+named\b", lowered))
    existential = bool(re.search(r"\bis\s+there\s+(?:any\s+)?", lowered))
    subject_first = bool(re.search(rf"\b(?:does|do|whether)\s+{EXISTENCE_SUBJECTS}\s+(?:\w+[\s,]+)*(?:{EXISTENCE_VERB_WORDS})\b", lowered))
    state_then_scope = bool(re.search(rf"\b(?:is|are)\b.+?\b{EXISTENCE_STATES}\b.+?\b(?:in|from|within)\s+{EXISTENCE_SUBJECTS}\b", lowered))
    anywhere_action = bool(re.search(r"\b(?:defined|declared|assigned|configured|checked|authorized|used|referenced|present|supported|implemented|found)\b.+?\banywhere\b", lowered))
    coordinated_state = bool(
        re.search(rf"\bwhether\b.+?\b{EXISTENCE_STATES}\b", lowered)
        or re.search(r"^(?:is|are)\b.+?\b(?:present|implemented|found|absent|missing)\b", lowered)
    )
    return (repository_scope and (subject_first or (existential and "there" in lowered) or state_then_scope or anywhere_action or named_concept)) or grouped_implemented or coordinated_state


def _explicit_existence_concepts(query: str) -> list[str]:
    """Read exact identifiers only from the clause that asks the existence question."""
    normalized = " ".join(query.replace("\n", " ").split())
    clauses = re.split(r"(?<=[?.])\s+", normalized)
    concepts = []
    for clause in clauses:
        if not _existence_intent(clause):
            continue
        focus = re.split(r"\b(?:such\s+as|do\s+not\s+map|do\s+not\s+invent)\b", clause, maxsplit=1, flags=re.IGNORECASE)[0]
        for identifier in EXPLICIT_CONCEPT_PATTERN.findall(focus):
            if identifier.lower() not in {item.lower() for item in concepts}:
                concepts.append(identifier)
    return concepts


def _extract_repository_existence_concepts(query: str) -> list[str]:
    """Extract repository-wide concepts only when the question asks for existence/absence."""
    if not _existence_intent(query):
        return []
    explicit = _explicit_existence_concepts(query)
    if len(explicit) >= 2:
        return explicit
    normalized = " ".join(query.replace("\n", " ").split())
    clauses = re.split(r"(?<=[?;])\s+|(?<=\.)\s+(?=[A-Z])", normalized)
    patterns = (
        rf"\bdoes\s+{EXISTENCE_SUBJECTS}\s+{EXISTENCE_VERBS}\s+(?P<concept>.+?)(?=(?:[?.;]|,\s*(?:and\s+)?whether\b))",
        rf"\bwhether\s+{EXISTENCE_SUBJECTS}\s+{EXISTENCE_VERBS}\s+(?:any\s+)?(?P<concept>.+?)(?=(?:[?.;]|,\s*(?:and\s+)?(?:do|does|is|are|whether)\b))",
        r"\bis\s+there\s+(?:any\s+)?(?P<concept>.+?)(?=(?:[?.;]|\s+anywhere\b))",
        r"\b(?:does|do)\s+(?P<concept>.+?)\s+exist(?:\s+anywhere)?(?=[?.;])",
        rf"\b(?:is|are)\s+(?P<concept>.+?)\s+{EXISTENCE_STATES}\s+(?:anywhere\s+)?(?:in|within|from)\s+{EXISTENCE_SUBJECTS}(?=[?.;])",
        rf"\bwhether\s+(?P<concept>.+?)\s+(?:is|are)\s+(?:actually\s+)?{EXISTENCE_STATES}(?:\s+(?:anywhere\s+)?(?:in|within|from)\s+{EXISTENCE_SUBJECTS})?(?=[?.;])",
        rf"\b(?:is|are)\s+(?P<concept>.+?)\s+(?:actually\s+)?{EXISTENCE_STATES}(?=[?.;])",
    )
    concepts = []
    for clause in clauses:
        if not _existence_intent(clause):
            continue
        for pattern in patterns:
          for match in re.finditer(pattern, clause, re.IGNORECASE):
            concept = match.group("concept").strip(" ,")
            concept = re.sub(r"^(?:any|a|an)\s+", "", concept, flags=re.IGNORECASE)
            concept = re.sub(r"^(?:actually|explicitly)\s+|\s+(?:actually|explicitly)$", "", concept, flags=re.IGNORECASE)
            concept = re.split(r"\s+such\s+as\s+", concept, maxsplit=1, flags=re.IGNORECASE)[0]
            concept = re.sub(r"\s+anywhere$", "", concept, flags=re.IGNORECASE)
            concept = re.sub(r"\s+(?:in|within)\s+(?:the\s+)?(?:repository|project|application|codebase|source tree|code)$", "", concept, flags=re.IGNORECASE)
            decomposed = _split_coordinated_existence_concepts(concept)
            for item in decomposed:
                if item and item.lower() not in {value.lower() for value in concepts}:
                    concepts.append(item)
    concepts = [item for item in concepts if item.lower() not in {"actually", "explicitly"}]
    if concepts and not (explicit and len(concepts) == 1):
        return concepts
    return explicit or concepts


def _split_coordinated_existence_concepts(value: str) -> list[str]:
    """Split only an existence-question list, preserving each multi-word semantic unit."""
    cleaned = re.sub(r"\s+", " ", value).strip(" ,.;?")
    cleaned = re.sub(r"^(?:whether|any|a|an|the|both|either)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:are|is)\s+(?:actually\s+)?$", "", cleaned, flags=re.IGNORECASE)
    has_list_syntax = "," in cleaned or bool(re.search(r"\s+(?:or|and)\s+", cleaned, re.IGNORECASE))
    if not has_list_syntax:
        return [cleaned] if cleaned else []
    if "," not in cleaned and re.search(r"\s+(?:and|or)\s+.+?\b(?:between|within|of|for)\b", cleaned, re.IGNORECASE):
        return [cleaned]
    parts = re.split(r"\s*,\s*|\s+(?:and|or)\s+", cleaned, flags=re.IGNORECASE)
    output = []
    for part in parts:
        part = re.sub(r"^(?:and|or|both|either|any|a|an|the)\s+", "", part.strip(" ,"), flags=re.IGNORECASE)
        slash_parts = [item.strip() for item in part.split("/") if item.strip()]
        if len(slash_parts) > 1:
            final_words = slash_parts[-1].split()
            shared_suffix = final_words[-1] if len(final_words) > 1 else ""
            for index, item in enumerate(slash_parts):
                candidate = item if index == len(slash_parts) - 1 or not shared_suffix else f"{item} {shared_suffix}"
                if candidate.lower() not in {existing.lower() for existing in output}:
                    output.append(candidate)
        elif part and part.lower() not in {existing.lower() for existing in output}:
            output.append(part)
    return output


def _concept_search_variants(concept: str) -> list[str]:
    cleaned = re.sub(r"\b(?:any|usual|some|the|a|an)\b", " ", concept, flags=re.IGNORECASE)
    alternatives = re.split(r"\s*(?:,|\bor\b|/)\s*", cleaned, flags=re.IGNORECASE)
    variants = []
    for alternative in alternatives:
        words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9]*", alternative) if word.lower() not in {"both", "either"}]
        if not words:
            continue
        phrase = " ".join(words)
        generic_tail = {"behavior", "handling", "usage", "configuration", "implementation", "logic", "support"}
        core = " ".join(words[:-1]) if len(words) > 1 and words[-1] in generic_tail else ""
        core_words = words[:-1] if core else []
        original_identifier = concept.strip() if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$.:-]*", concept.strip()) else ""
        for value in (phrase, "".join(word.title() for word in words), "_".join(words), "-".join(words), core,
                      "".join(word.title() for word in core_words), "_".join(core_words), "-".join(core_words), original_identifier):
            if value and value.lower() not in {item.lower() for item in variants}:
                variants.append(value)
    return variants


def _repository_concept_existence_search(project_id: str, concepts: list[str], rows: list, semantic_hits_by_concept: dict[str, list] | None = None) -> list[dict]:
    searches = []
    semantic_hits_by_concept = semantic_hits_by_concept or {}
    for concept in concepts:
        variants = _concept_search_variants(concept)
        phrase_variants = [variant.lower() for variant in variants]
        exact_symbol_hits, lexical_hits = [], []
        for row in rows:
            item = row_to_dict(row)
            symbol = str(item.get("symbol_name") or "")
            code = str(item.get("code") or "")
            haystack = f"{item.get('file_path', '')} {symbol} {code}".lower()
            symbol_compact = re.sub(r"[^a-z0-9]", "", symbol.lower())
            matched_symbols = [variant for variant in phrase_variants if re.sub(r"[^a-z0-9]", "", variant) == symbol_compact]
            identifier_aware = bool(EXPLICIT_CONCEPT_PATTERN.fullmatch(concept))
            matched_lexical = [
                variant for variant in phrase_variants
                if (
                    re.search(rf"(?<![A-Za-z0-9_]){re.escape(variant)}(?![A-Za-z0-9_])", haystack, re.IGNORECASE)
                    if identifier_aware else variant in haystack
                )
            ]
            path = str(item.get("file_path") or "").replace("\\", "/").lower()
            source_scope = "documentation" if path.endswith((".md", ".rst", ".txt")) or "/docs/" in f"/{path}" else ("test" if "/test/" in f"/{path}" or "/tests/" in f"/{path}" else "production")
            hit = {"chunk_id": item.get("id"), "file_path": item.get("file_path"), "symbol_name": item.get("symbol_name"), "start_line": item.get("start_line"), "end_line": item.get("end_line"), "source_scope": source_scope}
            if matched_symbols:
                exact_symbol_hits.append({**hit, "matched_variants": matched_symbols})
            if matched_lexical:
                lexical_hits.append({**hit, "matched_variants": matched_lexical})
        semantic_hits = semantic_hits_by_concept.get(concept, [])
        strong_semantic_hits = [hit for hit in semantic_hits if hit.get("similarity", 0) >= 0.65]
        if exact_symbol_hits or lexical_hits:
            result = "found"
        elif strong_semantic_hits:
            result = "uncertain"
        else:
            result = "not_found"
        unique_candidates = {hit.get("chunk_id") for hit in [*exact_symbol_hits, *lexical_hits, *strong_semantic_hits] if hit.get("chunk_id")}
        searches.append({
            "concept_searched": concept,
            "search_terms": variants,
            "search_scope": "all indexed source chunks in repository",
            "scanned_chunk_count": len(rows),
            "candidate_count": len(unique_candidates),
            "exact_symbol_hits": exact_symbol_hits,
            "lexical_hits": lexical_hits,
            "semantic_hits": semantic_hits,
            "existence_result": result,
        })
    return searches


def discover_security_modules(project_id: str, security_goal: str = "") -> list[dict]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT f.file_path, f.language, COUNT(c.id) AS chunk_count, GROUP_CONCAT(c.security_tags) AS tags, GROUP_CONCAT(c.symbol_name) AS symbols, GROUP_CONCAT(c.code, '\n') AS code
            FROM files f
            LEFT JOIN code_chunks c ON c.file_id = f.id
            WHERE f.project_id = ?
            GROUP BY f.id
            """,
            (project_id,),
        ).fetchall()
    candidates = []
    for row in rows:
        tags = sorted({tag for tag in (row["tags"] or "").split(",") if tag})
        symbols = sorted({symbol for symbol in (row["symbols"] or "").split(",") if symbol})
        text = f"{row['file_path']} {' '.join(symbols)} {' '.join(tags)} {row['code'] or ''}"
        signals = _query_match_signals(security_goal, text, symbols)
        keyword_hits = signals["lexical_match_count"]
        if tags or keyword_hits:
            confidence = confidence_for_tags(tags)
            if keyword_hits >= 2 and confidence != "High":
                confidence = "High"
            reasons = []
            if signals["exact_identifiers"]:
                reasons.append("exact identifier match: " + ", ".join(signals["exact_identifiers"][:3]))
            if signals["symbol_matches"]:
                reasons.append("symbol match: " + ", ".join(signals["symbol_matches"][:3]))
            if signals["lexical_terms"]:
                reasons.append("lexical matches: " + ", ".join(signals["lexical_terms"][:5]))
            if tags:
                reasons.append("semantic security metadata: " + ", ".join(tags[:4]))
            reason = "; ".join(reasons)
            candidates.append(
                {
                    "module_path": row["file_path"],
                    "language": row["language"],
                    "reason": reason,
                    "confidence": confidence,
                    "security_tags": tags,
                    "matching_symbols": symbols[:12],
                    "matching_chunk_count": row["chunk_count"],
                    "score": signals["exact_identifier_relevance"] * 12 + signals["symbol_relevance"] * 10 + signals["lexical_relevance"] * 6 + min(len(tags), 4) * 0.5,
                    "match_signals": signals,
                }
            )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:25]


DIRECT_MEMBER_CALL_PATTERN = re.compile(r"\b(?P<receiver>[A-Za-z_$][\w$]*)\s*\.\s*(?P<symbol>[A-Za-z_$][\w$]*)\s*\(")
DIRECT_CALL_IGNORED_RECEIVERS = {"this", "super", "system", "log", "logger", "assert", "response", "request"}
TRACE_GENERIC_TERMS = {
    "requirement", "trace", "exact", "repository", "source", "enforce", "identify", "protected",
    "http", "controller", "operation", "authorization", "expression", "authority", "authorities",
    "satisfy", "direct", "downstream", "implementation", "call", "boundary", "where", "ends",
    "clearly", "separate", "point", "service", "business", "state", "whether", "evidence", "fully",
    "support", "supports", "written", "part", "remain", "ambiguous", "claim", "invent", "document",
    "route", "prefix", "role", "hierarchy", "database", "behavior", "additional", "rules", "shown",
}


def _call_focus_tokens(text: str) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    tokens = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", expanded.lower()):
        normalized = token[:-1] if len(token) > 4 and token.endswith("s") else token
        if len(normalized) >= 3 and normalized not in TRACE_GENERIC_TERMS:
            tokens.add(normalized)
    return tokens


def _strict_downstream_trace(query: str, requested_roles: set[str]) -> bool:
    lowered = query.lower()
    return "needs_helper_implementation" in requested_roles and (
        "needs_requirement_trace" in requested_roles
        or any(marker in lowered for marker in ("direct downstream", "direct call", "call chain", "source boundary", "business implementation"))
    )


def _receiver_owner_type(receiver: str, caller_path: str, file_code: dict[str, list[str]]) -> str | None:
    source = "\n".join(file_code.get(caller_path, []))
    escaped = re.escape(receiver)
    patterns = (
        rf"\b([A-Z][A-Za-z0-9_$]*(?:<[^;=()]+>)?)\s+{escaped}\b",
        rf"\b{escaped}\s*:\s*([A-Z][A-Za-z0-9_.$]*)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return re.sub(r"<.*>", "", match.group(1)).split(".")[-1]
    if receiver and receiver[0].islower():
        return receiver[0].upper() + receiver[1:]
    return None


def _derive_direct_downstream_targets(query: str, rows: list, file_code: dict[str, list[str]]) -> list[dict]:
    """Resolve the most query-relevant member call made by an HTTP entry point."""
    calls = []
    query_focus = _call_focus_tokens(query)
    row_items = [row_to_dict(row) for row in rows]
    for caller in row_items:
        code = str(caller.get("code") or "")
        endpoint = bool(re.search(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\b|\b(?:router|app)\.(?:get|post|put|patch|delete)\s*\(", code, re.IGNORECASE))
        if not endpoint:
            continue
        for match in DIRECT_MEMBER_CALL_PATTERN.finditer(code):
            receiver, symbol = match.group("receiver"), match.group("symbol")
            if receiver.lower() in DIRECT_CALL_IGNORED_RECEIVERS:
                continue
            owner = _receiver_owner_type(receiver, caller.get("file_path") or "", file_code)
            call_text = f"{caller.get('symbol_name', '')} {receiver} {owner or ''} {symbol} {code}"
            relevance = len(query_focus.intersection(_call_focus_tokens(call_text)))
            identity_tokens = _call_focus_tokens(f"{caller.get('symbol_name', '')} {receiver} {owner or ''} {symbol}")
            relevance += 2 * len(query_focus.intersection(identity_tokens))
            lowered_query = query.lower()
            for verb, annotation in (("get", "getmapping"), ("post", "postmapping"), ("put", "putmapping"), ("patch", "patchmapping"), ("delete", "deletemapping")):
                if re.search(rf"\b{verb}\b", lowered_query) and annotation in code.lower():
                    relevance += 5
            calls.append({
                "caller_chunk_id": caller.get("id"), "caller_file_path": caller.get("file_path"),
                "caller_symbol": caller.get("symbol_name"), "receiver": receiver,
                "target_symbol": symbol, "preferred_owner_type": owner, "query_relevance": relevance,
            })
    if not calls:
        return []
    best_relevance = max(call["query_relevance"] for call in calls)
    selected = [call for call in calls if call["query_relevance"] == best_relevance]
    for call in selected:
        symbol_matches = [item for item in row_items if str(item.get("symbol_name") or "").lower() == call["target_symbol"].lower() and item.get("id") != call["caller_chunk_id"]]
        owner = str(call.get("preferred_owner_type") or "").lower()
        owner_matches = [item for item in symbol_matches if str(item.get("class_name") or "").split(".")[-1].lower() == owner] if owner else []
        resolved = owner_matches if owner_matches else symbol_matches
        if len(resolved) == 1:
            implementation = resolved[0]
            call.update(target_resolution="found", implementation_chunk_id=implementation.get("id"), resolved_owner_type=implementation.get("class_name"))
        elif len(resolved) > 1:
            call.update(target_resolution="ambiguous", implementation_chunk_id=None, resolved_owner_type=None)
        else:
            call.update(target_resolution="not_found", implementation_chunk_id=None, resolved_owner_type=None)
        call["candidate_implementation_chunk_ids"] = [item.get("id") for item in resolved]
    return selected


def retrieve_evidence_package(project_id: str, query: str, top_k: int, db_conn: sqlite3.Connection, module_id: str | None = None) -> dict:
    # ChromaDB returns distances where lower is better; vector_index.query converts each distance to hit.distance.
    # Re-ranking now happens after ChromaDB query via rescore_chunks.
    # Available metadata for code chunks: source_type, chunk_id, project_id, file_path, symbol_name, start_line, end_line, language, security_tags, tags, chunk_type.
    # Current top_k is supplied by the caller; Ask and Compare pass the same value.
    # File-type weighting is applied here through vector_index.rescore_chunks, not during indexing.
    settings = get_settings()
    existence_concepts = _extract_repository_existence_concepts(query)
    if existence_concepts:
        focused_variants = []
        for concept in existence_concepts:
            focused_variants.extend(_concept_search_variants(concept))
        expanded_query = query + (" " + " ".join(focused_variants[:8]) if focused_variants else "")
    else:
        expanded_query = expand_security_query(query)
    enumeration_intent = _has_enumeration_intent(query)
    manifest_component_intent = has_manifest_component_intent(query)
    terms = _query_features(expanded_query)["terms"]
    selected_file_path = _normalize_selected_file_path(module_id)
    vector_hits = vector_query(project_id, expanded_query, limit=max(top_k * 3, top_k), source_type="code")
    semantic_existence_hits: dict[str, list] = {}
    for concept in existence_concepts:
        concept_hits = vector_query(project_id, concept, limit=max(top_k * 3, top_k), source_type="code")
        semantic_existence_hits[concept] = [
            {
                "chunk_id": hit.metadata.get("chunk_id"),
                "file_path": hit.metadata.get("file_path"),
                "symbol_name": hit.metadata.get("symbol_name"),
                "distance": hit.distance,
                "similarity": max(0.0, min(1.0, 1.0 - float(hit.distance))) if hit.distance is not None else 0.0,
            }
            for hit in concept_hits
        ]
    vector_rank = {hit.metadata.get("chunk_id"): index for index, hit in enumerate(vector_hits) if hit.metadata.get("chunk_id")}
    vector_metadata = {hit.metadata.get("chunk_id"): hit.metadata for hit in vector_hits if hit.metadata.get("chunk_id")}
    vector_similarity = {
        hit.metadata.get("chunk_id"): max(0.0, min(1.0, 1.0 - float(hit.distance))) if hit.distance is not None else 1.0
        for hit in vector_hits
        if hit.metadata.get("chunk_id")
    }
    rows = db_conn.execute(
        """
        SELECT c.*, f.file_path, f.language
        FROM code_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.project_id = ?
        """,
        (project_id,),
    ).fetchall()
    repository_existence_searches = _repository_concept_existence_search(project_id, existence_concepts, rows, semantic_existence_hits)
    file_code = {}
    for row in rows:
        item = row_to_dict(row)
        file_code.setdefault(item["file_path"], []).append(item.get("code") or "")
    class_routes = {path: _extract_java_class_route("\n".join(parts)) for path, parts in file_code.items()}
    requested_roles = _extract_evidence_roles(query)
    strict_downstream_helper = _strict_downstream_trace(query, requested_roles)
    direct_downstream_calls = _derive_direct_downstream_targets(query, rows, file_code) if strict_downstream_helper else []
    resolved_implementation_ids = {
        call["implementation_chunk_id"] for call in direct_downstream_calls
        if call.get("target_resolution") == "found" and call.get("implementation_chunk_id")
    }
    candidates = []
    selected_file_candidates = []
    for row in rows:
        row_dict = row_to_dict(row)
        tags = row_dict.get("security_tags") or ""
        haystack = f"{row_dict['file_path']} {row_dict.get('symbol_name') or ''} {tags} {row_dict['code']}".lower()
        signals = _query_match_signals(query, haystack, [row_dict.get("symbol_name") or ""])
        expanded_term_hits = sum(1 for term in terms if term in haystack)
        lexical_score = signals["lexical_match_count"] * 2 + expanded_term_hits
        if row_dict["id"] in vector_rank:
            lexical_score += 8 - vector_rank[row_dict["id"]]
        if tags:
            lexical_score += 3
        selected_file_match = bool(selected_file_path and _normalize_selected_file_path(row_dict["file_path"]) == selected_file_path)
        selected_relevant = selected_file_match and _selected_chunk_relevant(row_dict, enumeration_intent)
        direct_downstream_implementation = row_dict["id"] in resolved_implementation_ids
        if lexical_score <= 0 and not selected_relevant and not direct_downstream_implementation:
            continue
        if (selected_relevant or direct_downstream_implementation) and lexical_score <= 0:
            lexical_score = 1
        base_similarity = vector_similarity.get(row_dict["id"], 0.0)
        route_metadata = _route_metadata(row_dict["code"], row_dict["language"], class_routes.get(row_dict["file_path"]))
        candidate = {
                "chunk_id": row_dict["id"],
                "id": row_dict["id"],
                "file_path": row_dict["file_path"],
                "symbol_name": row_dict["symbol_name"],
                "class_name": row_dict["class_name"],
                "start_line": row_dict["start_line"],
                "end_line": row_dict["end_line"],
                "language": row_dict["language"],
                "chunk_type": row_dict["chunk_type"],
                "security_tags": tags,
                "tags": [tag for tag in tags.split(",") if tag],
                "http_method": vector_metadata.get(row_dict["id"], {}).get("http_method") or route_metadata["http_method"] or "",
                "class_route": route_metadata["class_route"],
                "method_route": route_metadata["method_route"],
                "effective_route": route_metadata["effective_route"],
                "class_route_state": route_metadata["class_route_state"],
                "method_route_state": route_metadata["method_route_state"],
                "route_resolution_status": route_metadata["resolution_status"],
                "code_snippet": row_dict["code"],
                "critical_lines": critical_lines(row_dict["code"], row_dict["start_line"]),
                "base_similarity": base_similarity,
                "lexical_score": lexical_score,
                "lexical_relevance": signals["lexical_relevance"],
                "symbol_relevance": signals["symbol_relevance"],
                "exact_identifier_relevance": signals["exact_identifier_relevance"],
                "lexical_matches": signals["lexical_terms"],
                "symbol_matches": signals["symbol_matches"],
                "exact_identifier_matches": signals["exact_identifiers"],
                "vector_rank_bonus": max(0, 8 - vector_rank[row_dict["id"]]) if row_dict["id"] in vector_rank else 0,
                "selected_file_match": selected_file_match,
                "selected_file_boost": SELECTED_FILE_BOOST if selected_file_match else 0.0,
                "direct_downstream_implementation": direct_downstream_implementation,
                "manifest_component_intent": manifest_component_intent,
            }
        candidate_roles = _classify_evidence_roles(candidate, strict_downstream_helper)
        candidate["evidence_role_relevance"] = min(1.0, len(candidate_roles.intersection(requested_roles)) / max(1, len(requested_roles)))
        candidates.append(candidate)
        if selected_relevant:
            selected_file_candidates.append(candidate)
    ranked_candidates = rescore_chunks(candidates)
    for retrieval_rank, item in enumerate(ranked_candidates, 1):
        item["retrieval_rank"] = retrieval_rank
    ranked_selected = rescore_chunks(selected_file_candidates)
    retrieval_rank_by_id = {item["chunk_id"]: item["retrieval_rank"] for item in ranked_candidates}
    for item in ranked_selected:
        item["retrieval_rank"] = retrieval_rank_by_id.get(item["chunk_id"], len(ranked_candidates) + 1)
    source_chunks, removed_by_deduplication = _merge_selected_file_coverage(
        ranked_candidates,
        ranked_selected,
        top_k,
        settings.selected_file_min_chunks,
        settings.selected_file_max_chunks,
        enumeration_intent,
    )
    source_chunks = _apply_evidence_role_coverage(source_chunks, ranked_candidates, requested_roles, top_k, strict_downstream_helper)
    source_chunks, manifest_completeness = _apply_manifest_component_completeness(
        source_chunks, ranked_candidates, enumeration_intent, manifest_component_intent, selected_file_path, top_k
    )
    effective_packing_limit = max(top_k, len(manifest_completeness.get("added_or_retained_chunk_ids", [])))
    source_chunks, packing_diagnostics = _pack_overlapping_evidence(
        source_chunks, ranked_candidates, requested_roles, effective_packing_limit, strict_downstream_helper
    )
    source_chunks = _order_prompt_evidence(source_chunks, requested_roles, strict_downstream_helper)
    evidence_role_by_chunk = {item["chunk_id"]: sorted(_classify_evidence_roles(item, strict_downstream_helper)) for item in source_chunks}
    role_satisfaction_reason = {
        item["chunk_id"]: _role_satisfaction_reasons(item, strict_downstream_helper)
        for item in source_chunks if _role_satisfaction_reasons(item, strict_downstream_helper)
    }
    satisfied_roles = sorted({role for roles in evidence_role_by_chunk.values() for role in roles if role in requested_roles})
    unsatisfied_roles = sorted(set(requested_roles) - set(satisfied_roles))
    wiki_limit = max(0, settings.wiki_context_top_k)
    wiki_chunks = retrieve_wiki_context(project_id, query, limit=wiki_limit) if settings.wiki_context_enabled else []
    if module_id:
        module_wiki_chunks = retrieve_wiki_context(project_id, query, module_id=module_id, limit=wiki_limit) if settings.wiki_context_enabled else []
        wiki_chunks = _dedupe_wiki_chunks([*module_wiki_chunks, *wiki_chunks])[:wiki_limit]
    with db_conn:
        available_wiki_count = db_conn.execute("SELECT COUNT(*) FROM wiki_pages WHERE project_id = ?", (project_id,)).fetchone()[0]
    wiki_context = {
        "requested": bool(settings.wiki_context_enabled),
        "available_wiki_count": available_wiki_count,
        "candidate_wiki_chunk_count": max((item.get("candidate_wiki_chunk_count", 0) for item in wiki_chunks), default=0),
        "selected_wiki_chunk_count": len(wiki_chunks),
        "selected_wiki_chunks": wiki_chunks,
    }
    chunk_ids = [chunk["chunk_id"] for chunk in source_chunks] + [chunk["id"] for chunk in wiki_chunks if chunk.get("id")]
    retrieval_log = f"[Retrieval] project={project_id} query='{query}' expanded='{expanded_query}' top_k={top_k} chunk_ids={chunk_ids}"
    print(retrieval_log)
    return {
        "source_chunks": source_chunks,
        "wiki_chunks": wiki_chunks,
        "wiki_context": wiki_context,
        "chunk_ids": chunk_ids,
        "retrieval_log": retrieval_log,
        "diagnostics": {
            "vector_candidate_count": len(vector_hits),
            "lexical_candidate_count": len(candidates),
            "selected_file_candidates": len(ranked_selected),
            "selected_file_candidate_count": len(ranked_selected),
            "deduplicated_candidate_count": len({item["chunk_id"] for item in ranked_candidates}),
            "final_source_evidence_count": len(source_chunks),
            "wiki_orientation_count": len(wiki_chunks),
            "selected_file_chunks_in_final": sum(1 for item in source_chunks if item.get("selected_file_match")),
            "distinct_selected_file_symbols": len({
                item.get("symbol_name") or item["chunk_id"]
                for item in source_chunks if item.get("selected_file_match")
            }),
            "enumeration_intent": enumeration_intent,
            "manifest_component_intent": manifest_component_intent,
            "manifest_component_completeness": manifest_completeness,
            "candidates_removed_by_deduplication": removed_by_deduplication,
            **packing_diagnostics,
            "expanded_query": expanded_query,
            "repository_concept_existence_intent": bool(existence_concepts),
            "repository_existence_concepts_requested": existence_concepts,
            "repository_existence_searches": repository_existence_searches,
            "requested_evidence_roles": sorted(requested_roles),
            "satisfied_evidence_roles": satisfied_roles,
            "unsatisfied_evidence_roles": unsatisfied_roles,
            "direct_downstream_trace_mode": strict_downstream_helper,
            "direct_downstream_calls": direct_downstream_calls,
            "evidence_role_by_chunk": evidence_role_by_chunk,
            "evidence_role_by_chunk_id": evidence_role_by_chunk,
            "role_satisfaction_reason": role_satisfaction_reason,
            "evidence_role_limitations": [f"Requested evidence role not found in final source package: {role}" for role in unsatisfied_roles],
            "vector_candidates": [
                {"vector_rank": index + 1, "chunk_id": hit.metadata.get("chunk_id"), "file_path": hit.metadata.get("file_path"), "symbol_name": hit.metadata.get("symbol_name"), "distance": hit.distance}
                for index, hit in enumerate(vector_hits)
            ],
            "ranked_candidates": [
                {key: item.get(key) for key in ("retrieval_rank", "chunk_id", "file_path", "symbol_name", "base_similarity", "lexical_score", "lexical_relevance", "symbol_relevance", "exact_identifier_relevance", "evidence_role_relevance", "security_boost", "co_occurrence_boost", "final_score")}
                for item in ranked_candidates
            ],
        },
    }


EVIDENCE_ROLE_PHRASES = {
    "needs_endpoint_declarations": ("endpoint", "operation", "http method", "route mapping"),
    "needs_route_resolution": ("route", "path", "url", "mapping", "endpoint"),
    "needs_authority_checks": ("authority", "authorities", "access", "authorized", "preauthorize", "permission"),
    "needs_user_assignments": ("configured user", "demo user", "user assignment", "user-to-authority", "which users", "users can access", "authority assignment"),
    "needs_security_configuration": ("security configuration", "configured user", "demo user", "user assignment", "authority assignment"),
    "needs_helper_implementation": ("helper", "dependency", "implementation", "where enforced", "downstream"),
    "needs_requirement_trace": ("requirement trace", "trace requirement", "requirement"),
    "needs_claim_definition": ("claim name", "claim configuration", "roles claim", "authorities claim", "custom claim"),
    "needs_claim_population": ("claim populated", "claim is populated", "populate", "put into", "added to the claim", "where the claim"),
    "needs_token_creation": ("token created", "jwt is created", "jwt created", "create jwt", "token creation", "sign token", "jwt creation"),
    "needs_authority_conversion": ("converts", "converted", "become authorities", "granted authorities", "authority converter", "authority prefix", "role_", "scope_"),
    "needs_authentication_wiring": ("attached to jwt authentication", "attached", "authentication wiring", "resource server", "jwt authentication", "converter configuration"),
    "needs_credential_authentication": ("credentials are authenticated", "credential authentication", "credential verification", "verify credentials", "password check", "password verification", "login flow", "authentication flow"),
    "needs_token_validation": ("token validation", "validate token", "validated", "token decoder", "jwt decoder", "incoming jwt", "incoming token", "signature verification", "verify token"),
}


def _extract_evidence_roles(query: str) -> set[str]:
    lowered = query.lower()
    roles = {role for role, phrases in EVIDENCE_ROLE_PHRASES.items() if any(phrase in lowered for phrase in phrases)}
    if _has_manifest_component_enumeration_intent(query):
        roles.add("needs_endpoint_declarations")
    if _has_bootstrap_privileged_identity_intent(lowered):
        roles.update(("needs_user_assignments", "needs_authority_checks"))
    if _has_state_dependent_write_restriction_intent(lowered):
        roles.add("needs_authority_checks")
    if _has_prerequisite_authorization_intent(lowered):
        roles.add("needs_authority_checks")
    if _has_authority_mutation_intent(lowered):
        roles.add("needs_authority_checks")
    privileged_role_intent = _has_privileged_role_distinction_intent(lowered)
    if privileged_role_intent:
        roles.add("needs_authority_checks")
        if _has_role_establishment_intent(lowered):
            roles.add("needs_user_assignments")
    if "claim" in lowered and any(marker in lowered for marker in ("custom", "roles", "authorities", "permissions", "scope")) and any(marker in lowered for marker in ("convert", "become", "mapped", "claim name")):
        roles.add("needs_claim_definition")
    conversion_question = "needs_authority_conversion" in roles
    explicit_enforcement = any(marker in lowered for marker in ("require authority", "requires authority", "authority check", "which endpoints", "who can access", "permits access", "preauthorize"))
    if conversion_question and not explicit_enforcement:
        roles.discard("needs_authority_checks")
    # A conceptual authentication-versus-authorization trace asks for a chain,
    # not merely the isolated nouns that happen to match the phrase table.
    # Plan every explicitly requested stage so coverage can be evaluated before
    # redundant supporting chunks are packed.
    credential_stage = any(marker in lowered for marker in ("credential", "password", "login", "sign in", "signin")) and any(
        marker in lowered for marker in ("authenticat", "verif", "check", "login", "sign in", "signin")
    )
    issuance_stage = any(marker in lowered for marker in ("token", "jwt")) and any(
        marker in lowered for marker in ("issu", "creat", "generat", "mint", "sign")
    )
    validation_stage = any(marker in lowered for marker in ("incoming token", "incoming jwt", "token validation", "jwt validation", "decoder", "verif", "validated"))
    conversion_stage = any(marker in lowered for marker in ("convert", "authorit", "principal", "authentication object", "security identity")) and any(
        marker in lowered for marker in ("token", "jwt", "claim", "incoming")
    )
    protected_stage = any(marker in lowered for marker in ("protected", "access", "authoriz", "permission", "authority", "role")) and any(
        marker in lowered for marker in ("endpoint", "operation", "product", "route", "access", "check", "declaration", "flow")
    )
    if credential_stage:
        roles.add("needs_credential_authentication")
    if issuance_stage:
        roles.add("needs_token_creation")
    if validation_stage:
        roles.update(("needs_token_validation", "needs_authentication_wiring"))
    if conversion_stage:
        roles.update(("needs_authority_conversion", "needs_authentication_wiring"))
    if protected_stage:
        roles.update(("needs_endpoint_declarations", "needs_authority_checks"))
    return roles


def _has_bootstrap_privileged_identity_intent(lowered_query: str) -> bool:
    """Recognize setup flows that establish the first privileged identity."""
    setup_action = bool(re.search(
        r"\b(?:bootstrap(?:s|ped|ping)?|initiali[sz](?:e|es|ed|ing|ation)|"
        r"provision(?:s|ed|ing)?|set[ -]?up|setup|creat(?:e|es|ed|ing)|"
        r"establish(?:es|ed|ing)?)\b",
        lowered_query,
    ))
    container = bool(re.search(
        r"\b(?:workspace|tenant|organi[sz]ation|household|project|account|team)s?\b",
        lowered_query,
    ))
    initial_privileged_identity = bool(re.search(
        r"\b(?:first|initial)\s+(?:privileged\s+member|owner|administrator|admin)\b",
        lowered_query,
    ))
    return setup_action and container and initial_privileged_identity


def _has_state_dependent_write_restriction_intent(lowered_query: str) -> bool:
    """Recognize lifecycle-state write policies without requiring an RBAC actor."""
    mutation = bool(re.search(
        r"\b(?:write|writes|writing|written|create|creates|created|creating|"
        r"update|updates|updated|updating|delete|deletes|deleted|deleting|"
        r"modify|modifies|modified|modifying|edit|edits|edited|editing|"
        r"insert|inserts|inserted|inserting|remove|removes|removed|removing|"
        r"mutate|mutates|mutated|mutating|mutation|mutations|change|changes|changed|changing|"
        r"field|fields|operation|operations|action|actions)\b",
        lowered_query,
    ))
    restriction = bool(re.search(
        r"\b(?:block|blocks|blocked|blocking|deny|denies|denied|forbid|forbids|forbidden|"
        r"prevent|prevents|prevented|preventing|disallow|disallows|disallowed|disallowing|read[ -]?only|"
        r"cannot\s+(?:be\s+)?(?:writ|creat|updat|delet|modif|edit|insert|remov|mutat|chang)|"
        r"locked\s+against\s+(?:write|writes|writing|changes?))\b",
        lowered_query,
    ))
    lifecycle_state = bool(re.search(
        r"\b(?:clos(?:e|ed|ure)|frozen|freez(?:e|es|ing)|finaliz(?:e|es|ed|ing|ation)|"
        r"archiv(?:e|es|ed|ing)|lock(?:ed|s|ing)|suspend(?:ed|s|ing)|"
        r"complet(?:e|es|ed|ing|ion)|publish(?:ed|es|ing)|publication|settle(?:d|s|ment)|"
        r"terminat(?:e|es|ed|ing|ion))\b",
        lowered_query,
    ))
    adverse_state = bool(re.search(
        r"\b(?:closed|frozen|finalized|archived|locked|suspended|settled|terminated)\b",
        lowered_query,
    ))
    modal_policy_question = bool(re.search(r"^\s*(?:can|may)\b", lowered_query)) and bool(
        re.search(r"\b(?:after|once|when)\b", lowered_query)
    ) and adverse_state
    return mutation and lifecycle_state and (restriction or modal_policy_question)


def _has_prerequisite_authorization_intent(lowered_query: str) -> bool:
    """Recognize actor-operation-condition authorization questions conservatively."""
    actor = bool(re.search(
        r"\b(?:user|caller|principal|account|subject|member|authenticated\s+(?:actor|user|caller|principal))\b",
        lowered_query,
    ))
    protected_operation = bool(re.search(
        r"\b(?:join|create|register|activate|add|enroll|modify|access|perform|write|update|delete)\w*\b",
        lowered_query,
    ))
    condition = bool(re.search(
        r"\b(?:without|only\s+if|unless|requires?|required|prerequisite|must\s+have|"
        r"must\s+(?:there\s+)?(?:already\s+)?be|allowed\s+when|permitted\s+when|blocked\s+unless|before)\b",
        lowered_query,
    ))
    authorization_orientation = bool(re.search(
        r"\b(?:can|may|allowed|permitted|denied|blocked|enforc\w*|prevent\w*|bypass\w*|authoriz\w*|policy|access)\b",
        lowered_query,
    ))
    actor_operation_question = actor and protected_operation and condition and authorization_orientation
    abstract_policy_question = bool(re.search(r"\b(?:operation|action)\b", lowered_query)) and condition and bool(
        re.search(r"\b(?:allowed|permitted|denied|blocked|enforc\w*|policy)\b", lowered_query)
    )
    proof_of_actor_gate = actor and protected_operation and bool(re.search(
        r"\b(?:condition|prerequisite)\b.*\b(?:satisfied|enforced|proves?|source)\b|"
        r"\b(?:proves?|source)\b.*\b(?:condition|prerequisite|enforced)\b",
        lowered_query,
    ))
    return actor_operation_question or abstract_policy_question or proof_of_actor_gate


def _has_authority_mutation_intent(lowered_query: str) -> bool:
    """Recognize protected authority-value changes without matching ordinary edits."""
    actor = bool(re.search(
        r"\b(?:user|caller|account|principal|subject|recipient|client|member|operator)\b",
        lowered_query,
    ))
    authority_value = bool(re.search(
        r"\b(?:role|permission|privilege|authority|entitlement|access\s+(?:level|scope)|scope|"
        r"security\s+(?:tier|level)|assigned\s+(?:level|tier)|authorization\s+level)\b",
        lowered_query,
    ))
    mutation = bool(re.search(
        r"\b(?:chang(?:e|es|ed|ing)|alter(?:s|ed|ing)?|modif(?:y|ies|ied|ying)|overrid(?:e|es|den|ing)|"
        r"replac(?:e|es|ed|ing)|choos(?:e|es|ing)|upgrade(?:s|d|ing)?|downgrade(?:s|d|ing)?|"
        r"preserv(?:e|es|ed|ing)|rewrit(?:e|es|ten|ing)|"
        r"select\s+another|set\s+(?:to\s+)?another|assign\s+(?:a\s+)?different|keep\s+unchanged)\b",
        lowered_query,
    ))
    protected_workflow = bool(re.search(
        r"\b(?:authorization|access|approval|acceptance|accepting|accepted|activate|activation|activating|"
        r"enroll|enrollment|registration|membership|grant|assignment|protected\s+operation|"
        r"security\s+decision|policy[- ]controlled\s+workflow|workflow|token)\b",
        lowered_query,
    ))
    return actor and authority_value and mutation and protected_workflow


def _has_privileged_role_distinction_intent(lowered_query: str) -> bool:
    """Recognize authorization-role comparisons without treating every use of 'role' as security."""
    explicit_privilege = bool(re.search(r"\b(?:privileg(?:e|ed)|elevated|higher[- ]privilege|lower[- ]privilege)\b", lowered_query))
    authorization_context = bool(re.search(r"\b(?:role|authorit(?:y|ies|zation|zed)|permission|access|privileg(?:e|ed|es))\b", lowered_query))
    comparative_or_decision = bool(re.search(
        r"\b(?:distinguish(?:ed|es)?|differ(?:s|ent)?|versus|vs\.?|ordinary|standard|lower[- ]privilege|"
        r"what makes|determin(?:e|es|ed|ing)|check(?:s|ed|ing)? whether)\b",
        lowered_query,
    ))
    establishment = _has_role_establishment_intent(lowered_query)
    return authorization_context and (
        (explicit_privilege and (comparative_or_decision or establishment))
        or (comparative_or_decision and bool(re.search(r"\b(?:permission|access|authoriz|elevated|privileged)\w*\b", lowered_query)))
    )


def _has_role_establishment_intent(lowered_query: str) -> bool:
    action = r"(?:establish(?:es|ed|ing)?|assign(?:s|ed|ing|ment)?|creat(?:e|es|ed|ing)|initializ(?:e|es|ed|ing)|bootstrap(?:s|ped|ping)?|stor(?:e|es|ed|ing)|persist(?:s|ed|ing|ence)?|grant(?:s|ed|ing)?)"
    role_subject = r"(?:role|authorit(?:y|ies)|privileg(?:e|ed|es)|identity|principal|user)"
    nearby_words = r"(?:\W+\w+){0,6}\W+"
    return bool(re.search(rf"\b(?:{action}{nearby_words}{role_subject}|{role_subject}{nearby_words}{action})\b", lowered_query))


GENERIC_POLICY_DECISION_PATTERN = re.compile(
    r"^[ \t]*(?:allow|permit|deny|authorize)\s+"
    r"(?:create|read|write|update|delete)(?:\s*,\s*(?:create|read|write|update|delete))*"
    r"\s*:?\s*(?:if|when|unless)\s+(?P<predicate>[^;\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
GENERIC_POLICY_IDENTITY_MARKERS = (
    "auth", "authenticated", "signedin", "signed_in", "signed-in", "principal", "caller",
    "subject", "identity", "userid", "user_id", "uid", "owner", "member", "admin", "role",
)


def _has_conditional_authorization_policy(code: str) -> bool:
    """Recognize concrete, generic policy decisions without treating CRUD prose as enforcement."""
    for match in GENERIC_POLICY_DECISION_PATTERN.finditer(code):
        predicate = match.group("predicate").strip()
        lowered = predicate.lower().replace(" ", "")
        helper_call = bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\([^\n;]*\)", predicate))
        identity_predicate = any(marker in lowered for marker in GENERIC_POLICY_IDENTITY_MARKERS) and bool(
            re.search(r"(?:==|!=|\bin\b|\bis\b|&&|\|\||\band\b|\bor\b)", predicate, re.IGNORECASE)
        )
        if helper_call or identity_predicate:
            return True
    return False


def _classify_evidence_roles(item: dict, strict_downstream_helper: bool = False) -> set[str]:
    text = f"{item.get('file_path', '')} {item.get('symbol_name', '')} {item.get('security_tags', '')} {item.get('code_snippet', '')}".lower()
    roles = set()
    manifest_component = item.get("chunk_type") == "xml_component" and bool(item.get("manifest_component_intent"))
    if manifest_component:
        roles.add("needs_endpoint_declarations")
        if re.search(
            r"\b[A-Za-z_][\w.-]*:(?:permission|readPermission|writePermission)\s*=",
            item.get("code_snippet", ""),
            re.IGNORECASE,
        ):
            roles.add("needs_authority_checks")
    if item.get("http_method") or any(marker in text for marker in ("@getmapping", "@postmapping", "@deletemapping", "@putmapping", "@patchmapping", "@route(", "router.get(", "router.post(", "app.get(", "app.post(")):
        roles.add("needs_endpoint_declarations")
    if item.get("class_route") is not None or item.get("method_route") is not None:
        roles.add("needs_route_resolution")
    if any(marker in text for marker in ("hasauthority", "hasanyauthority", "hasrole", "preauthorize", "secured", "rolesallowed", "require_permission", "permission_required", "authorize(")) or _has_conditional_authorization_policy(item.get("code_snippet", "")):
        roles.add("needs_authority_checks")
    principal_indicator = any(marker in text for marker in (".username(", "withuser(", "withusername(", "user.withusername(", "userdetails.builder", "new user(", "principal(", "username ="))
    assignment_indicator = any(marker in text for marker in (".authorities(", ".roles(", "grantedauthority", "simplegrantedauthority", "authoritylist", "authorities =", "roles ="))
    if principal_indicator and assignment_indicator:
        roles.add("needs_user_assignments")
    if _has_concrete_identity_role_assignment(item.get("file_path", ""), item.get("code_snippet", "")):
        roles.add("needs_user_assignments")
    if any(marker in text for marker in ("websecurity", "securityconfig", "securityfilterchain", "httpsecurity", "userdetailsservice", "inmemoryuserdetailsmanager")):
        roles.add("needs_security_configuration")
    if strict_downstream_helper:
        if item.get("direct_downstream_implementation"):
            roles.add("needs_helper_implementation")
    elif not item.get("http_method") and any(marker in text for marker in ("helper", "service", "repository", "permission", "authorize", "filter")):
        roles.add("needs_helper_implementation")
    if item.get("file_path") and item.get("start_line"):
        roles.add("needs_requirement_trace")
    code_text = item.get("code_snippet", "")
    claim_name = "setauthoritiesclaimname" in text or bool(re.search(r"\b(?:authorit(?:y|ies)|role|scope|permission)[a-z0-9_]*claim[a-z0-9_]*\s*=", code_text, re.IGNORECASE))
    if claim_name:
        roles.add("needs_claim_definition")
    claim_container = any(marker in text for marker in ("claims.put(", "claims[", "claims.update(", ".withclaim(", "addclaim(", "setclaim(", "claim("))
    claim_value = any(marker in text for marker in ("authorit", "roles", "permissions", "scopes"))
    if claim_container and claim_value:
        roles.add("needs_claim_population")
    token_builder = any(marker in text for marker in ("jwt.create(", "jwtbuilder", "jwts.builder", "encode(", "signwith(", ".sign(", "createtoken", "issuejwt", "createjwt", "minttoken(", "mint_token(", "tokenfactory.issue", "token_factory.issue", "tokenissuer.issue", "token_issuer.issue"))
    if token_builder:
        roles.add("needs_token_creation")
    authority_converter = any(marker in text for marker in ("jwtgrantedauthoritiesconverter", "setjwtgrantedauthoritiesconverter", "setauthorityprefix", "setauthoritiesclaimname", "claimstoauthorities", "claims_to_authorities", "claimstopermissions", "claims_to_permissions", "authoritymapper", "authority_mapper", "permissionmapper", "permission_mapper"))
    if authority_converter and any(marker in text for marker in ("converter", "claim", "authorit", "role", "scope")):
        roles.add("needs_authority_conversion")
    auth_wiring = any(marker in text for marker in ("setjwtgrantedauthoritiesconverter", ".jwtauthenticationconverter(", "oauth2resourceserver", "addfilter", "authenticationprovider", "bearertoken", "authmiddleware", "authenticationmiddleware", "useauthentication", "addauthentication", "identityadapter", "identity_adapter", "tokenauthenticator", "token_authenticator"))
    if auth_wiring:
        roles.add("needs_authentication_wiring")
    credential_verifier = any(marker in text for marker in (
        "authenticationmanager.authenticate", "authenticationprovider.authenticate", "passwordencoder.matches",
        "checkpassword(", "check_password(", "verifypassword(", "verify_password(", "bcrypt.compare",
        "argon2.verify", "compare_digest(", "validatecredentials(", "verifycredentials(", "authenticate(credentials",
    ))
    credential_inputs = any(marker in text for marker in ("password", "credential", "username", "login"))
    if credential_verifier and credential_inputs:
        roles.add("needs_credential_authentication")
    token_validator = any(marker in text for marker in (
        "jwtdecoder", "jwkseturi", "decoder.decode(", "verifier.verify(", "verifytoken(", "verify_token(",
        "validatetoken(", "validate_token(", "jwt.verify(", "decode_token(", "decodejwt(", "decode_jwt(",
        "signaturevalidator", "tokenvalidator", "token_decoder",
    ))
    if token_validator:
        roles.add("needs_token_validation")
    return roles


def _has_concrete_identity_role_assignment(file_path: str, code: str) -> bool:
    """Detect a real identity-to-role construction/write while rejecting descriptive prose."""
    extension = os.path.splitext(str(file_path or "").lower())[1]
    if extension in {".md", ".txt", ".xml", ".rst", ".adoc"}:
        return False
    executable = re.sub(r"/\*.*?\*/", " ", str(code or ""), flags=re.DOTALL)
    executable = "\n".join(line for line in executable.splitlines() if not line.lstrip().startswith(("//", "#", "*")))
    identity_association = bool(re.search(
        r"\b(?:user_?id|uid|principal(?:_?id)?|subject(?:_?id)?|identity(?:_?id)?|current_?user|currentprincipal|currentidentity)\b",
        executable,
        re.IGNORECASE,
    ))
    explicit_role_assignment = bool(re.search(
        r"(?:['\"](?:role|authority|privilege)['\"]|\b(?:role|authority|privilege)\b)\s*(?:to|:|=)\s*[A-Za-z_'\"]",
        executable,
        re.IGNORECASE,
    ))
    persistent_write = bool(re.search(r"\.(?:set|save|insert|put|create|add|update|upsert)\s*\(", executable, re.IGNORECASE))
    constructed_assignment = bool(re.search(
        r"\b[A-Z][A-Za-z0-9_]*\s*\([^)]*\b(?:role|authority|privilege)\s*=",
        executable,
        re.IGNORECASE | re.DOTALL,
    ))
    return identity_association and explicit_role_assignment and (persistent_write or constructed_assignment)


def _role_satisfaction_reasons(item: dict, strict_downstream_helper: bool = False) -> dict[str, str]:
    roles = _classify_evidence_roles(item, strict_downstream_helper)
    labels = {
        "needs_user_assignments": "Chunk contains both a principal/user construction indicator and an authority/role assignment indicator.",
        "needs_endpoint_declarations": "Chunk contains a requested HTTP endpoint or Android manifest component declaration.",
        "needs_route_resolution": "Chunk contains parser-derived route metadata.",
        "needs_authority_checks": "Chunk contains an authority/role authorization construct.",
        "needs_security_configuration": "Chunk contains a recognized security configuration construct.",
        "needs_claim_definition": "Chunk configures the claim name consumed for authorities.",
        "needs_claim_population": "Chunk mutates a claim container with an authority/role-like value.",
        "needs_token_creation": "Chunk contains a concrete JWT/token builder, signing operation, or creation call.",
        "needs_authority_conversion": "Chunk contains a claim-to-authority converter configuration construct.",
        "needs_authentication_wiring": "Chunk attaches a converter/filter/provider to an authentication or resource-server pipeline.",
        "needs_credential_authentication": "Chunk contains a concrete credential or password verification operation.",
        "needs_token_validation": "Chunk contains a concrete token decoder, verifier, signature validator, or validation configuration.",
        "needs_helper_implementation": "Chunk implements the directly called downstream target resolved from the selected entry point.",
    }
    return {role: labels[role] for role in roles if role in labels}


def _order_prompt_evidence(chunks: list[dict], requested_roles: set[str], strict_downstream_helper: bool = False) -> list[dict]:
    """Keep retrieval rank auditable while producing a coverage-aware prompt order."""
    def classify(item: dict) -> tuple[int, str]:
        roles = _classify_evidence_roles(item, strict_downstream_helper)
        path = str(item.get("file_path") or "").replace("\\", "/").lower()
        is_test = "/test/" in path or "/tests/" in path or path.endswith("test.java") or path.endswith("tests.java")
        if item.get("selected_file_match") and "needs_endpoint_declarations" in roles:
            return 1, "target_primary"
        if roles.intersection(requested_roles) and not is_test:
            return 2, "required_supporting_role"
        if roles.intersection(requested_roles):
            return 3, "supporting_test_evidence"
        if item.get("chunk_type") == "class_route_context" or item.get("class_route_state") == "present":
            return 4, "route_or_class_context"
        if "needs_helper_implementation" in roles:
            return 5, "helper_or_execution_context"
        return 6, "optional_context"

    ordered = []
    for item in chunks:
        priority, label = classify(item)
        item["evidence_priority_class"] = label
        item["_prompt_priority"] = priority
        ordered.append(item)
    ordered.sort(key=lambda item: (item["_prompt_priority"], item.get("retrieval_rank", 10**9), item["chunk_id"]))
    for position, item in enumerate(ordered, 1):
        item.pop("_prompt_priority", None)
        item["prompt_position"] = position
    return ordered


def _evidence_role_strength(item: dict, role: str) -> int:
    """Prefer concrete implementations while retaining valid call-site evidence."""
    text = str(item.get("code_snippet") or "").lower()
    if role == "needs_token_creation":
        concrete = any(marker in text for marker in (
            "jwt.create(", "jwtbuilder", "jwts.builder", "signwith(", ".sign(",
            "tokenfactory.issue", "token_factory.issue", "tokenissuer.issue", "token_issuer.issue",
        ))
        return 2 if concrete else 1
    if role == "needs_token_validation":
        configured_decoder = any(marker in text for marker in (
            "jwtdecoder", "jwkseturi", "signaturevalidator", "tokenvalidator", "token_decoder",
        ))
        return 2 if configured_decoder else 1
    if role == "needs_credential_authentication":
        concrete = any(marker in text for marker in (
            "authenticationmanager.authenticate", "authenticationprovider.authenticate", "passwordencoder.matches",
            "argon2.verify", "bcrypt.compare", "compare_digest(",
        ))
        return 2 if concrete else 1
    return 1


def _apply_evidence_role_coverage(current: list[dict], ranked: list[dict], requested: set[str], top_k: int, strict_downstream_helper: bool = False) -> list[dict]:
    result = _dedupe_source_chunks(current)
    for role in sorted(requested):
        current_representatives = [item for item in result if role in _classify_evidence_roles(item, strict_downstream_helper)]
        best_available = next((item for item in ranked if role in _classify_evidence_roles(item, strict_downstream_helper)), None)
        current_strength = max((_evidence_role_strength(item, role) for item in current_representatives), default=0)
        available_strength = max((_evidence_role_strength(item, role) for item in ranked if role in _classify_evidence_roles(item, strict_downstream_helper)), default=0)
        if current_representatives and current_strength >= available_strength:
            continue
        result_ids = {entry["chunk_id"] for entry in result}
        replacement = next((
            item for item in ranked
            if role in _classify_evidence_roles(item, strict_downstream_helper)
            and _evidence_role_strength(item, role) == available_strength
            and item["chunk_id"] not in result_ids
        ), best_available if best_available and best_available["chunk_id"] not in result_ids else None)
        if not replacement:
            continue
        if len(result) < top_k:
            result.append(replacement)
            continue
        # Replace the lowest-ranked redundant/optional chunk. Never evict the
        # sole representative of a role already covered by the package.
        coverage_strengths = {
            covered: max((_evidence_role_strength(item, covered) for item in result if covered in _classify_evidence_roles(item, strict_downstream_helper)), default=0)
            for covered in requested
        }

        def removable(index: int) -> bool:
            remaining = [item for position, item in enumerate(result) if position != index]
            remaining.append(replacement)
            return all(
                max((_evidence_role_strength(item, covered) for item in remaining if covered in _classify_evidence_roles(item, strict_downstream_helper)), default=0) >= strength
                for covered, strength in coverage_strengths.items()
            )

        replace_index = next((
            index for index in range(len(result) - 1, -1, -1)
            if not result[index].get("selected_file_match")
            and removable(index)
        ), None)
        if replace_index is None:
            replace_index = next((
                index for index in range(len(result) - 1, -1, -1)
                if removable(index)
            ), None)
        if replace_index is not None:
            result[replace_index] = replacement
    return _dedupe_source_chunks(result)[:top_k]


def _apply_manifest_component_completeness(
    current: list[dict], ranked: list[dict], enumeration_intent: bool,
    manifest_component_intent: bool, selected_file_path: str | None, top_k: int,
) -> tuple[list[dict], dict]:
    """Collect every in-scope manifest component only for bounded component enumeration."""
    active = enumeration_intent and manifest_component_intent
    if not active:
        return list(current), {
            "active": False,
            "candidate_count": 0,
            "added_chunk_ids": [],
            "added_or_retained_chunk_ids": [],
        }
    component_candidates = [
        item for item in ranked
        if item.get("chunk_type") == "xml_component"
        and (
            not selected_file_path
            or _normalize_selected_file_path(item.get("file_path")) == selected_file_path
        )
    ]
    active_manifest_path = selected_file_path
    if not active_manifest_path and component_candidates:
        active_manifest_path = _normalize_selected_file_path(component_candidates[0].get("file_path"))
    components = [
        item for item in component_candidates
        if not active_manifest_path
        or _normalize_selected_file_path(item.get("file_path")) == active_manifest_path
    ]
    components = sorted(
        _dedupe_source_chunks(components),
        key=lambda item: (
            _normalize_selected_file_path(item.get("file_path")),
            int(item.get("start_line") or 0),
            str(item.get("symbol_name") or ""),
        ),
    )
    component_ids = {item["chunk_id"] for item in components}
    current_ids = {item["chunk_id"] for item in current}
    final_limit = max(top_k, len(components))
    result = list(components)
    for item in current:
        if (
            item.get("chunk_type") != "xml_component"
            and item["chunk_id"] not in component_ids
            and len(result) < final_limit
        ):
            result.append(item)
    return result, {
        "active": True,
        "candidate_count": len(components),
        "added_chunk_ids": [item["chunk_id"] for item in components if item["chunk_id"] not in current_ids],
        "added_or_retained_chunk_ids": [item["chunk_id"] for item in components],
        "scope": active_manifest_path or "no_manifest_components_found",
        "final_limit": final_limit,
    }


STRUCTURAL_PARENT_TYPES = {"class", "interface", "object"}
STRUCTURAL_CHILD_TYPES = {"function", "method", "constructor", "async_function"}
SECURITY_RESOURCE_MARKERS = ("auth", "authoriz", "permission", "owner", "member", "admin", "role", "security", "login", "sign_in", "signin")
PACKING_ACTION_FAMILIES = {
    "setup": ("bootstrap", "initialize", "initialise", "provision", "setup", "create", "establish"),
    "join": ("join", "attach", "enroll", "accept"),
    "update": ("update", "modify", "edit", "change", "replace"),
    "delete": ("delete", "remove", "archive", "terminate"),
}


def _packing_action_families(text: str) -> set[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or "")).lower()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return {
        family for family, markers in PACKING_ACTION_FAMILIES.items()
        if any(marker in tokens or any(token.startswith(marker) for token in tokens) for marker in markers)
    }


def _child_preserves_parent_action_concepts(parent: dict, child: dict) -> bool:
    """Reject a sibling action when it does not preserve why the parent matched."""
    parent_signals = " ".join(
        str(signal) for signal in [*(parent.get("lexical_matches") or []), *(parent.get("exact_identifier_matches") or [])]
    )
    required_families = _packing_action_families(parent_signals)
    if not required_families:
        return True
    child_families = _packing_action_families(child.get("symbol_name") or "")
    if not child_families:
        return True
    return bool(required_families.intersection(child_families))


def _pack_overlapping_evidence(current: list[dict], ranked: list[dict], requested: set[str], top_k: int, strict_downstream_helper: bool = False) -> tuple[list[dict], dict]:
    """Remove only structurally proven overlap after ranking and role coverage."""
    result = list(_dedupe_source_chunks(current))
    containment_replacements = []
    for parent in list(result):
        if parent.get("chunk_type") not in STRUCTURAL_PARENT_TYPES:
            continue
        parent_roles = _classify_evidence_roles(parent, strict_downstream_helper).intersection(requested)
        parent_signals = set(parent.get("lexical_matches") or []) | set(parent.get("exact_identifier_matches") or [])
        parent_score = float(parent.get("final_score") or 0.0)
        children = []
        for child in ranked:
            if child.get("chunk_type") not in STRUCTURAL_CHILD_TYPES or child.get("file_path") != parent.get("file_path"):
                continue
            if not (int(parent.get("start_line") or 0) <= int(child.get("start_line") or -1) and int(child.get("end_line") or -1) <= int(parent.get("end_line") or 0)):
                continue
            child_roles = _classify_evidence_roles(child, strict_downstream_helper).intersection(requested)
            child_signals = set(child.get("lexical_matches") or []) | set(child.get("exact_identifier_matches") or [])
            independently_relevant = bool(child_roles or child_signals)
            comparable = float(child.get("final_score") or 0.0) >= parent_score * 0.6
            concept_preserving = _child_preserves_parent_action_concepts(parent, child)
            if independently_relevant and comparable and concept_preserving:
                children.append(child)
        def child_order(item: dict) -> tuple:
            symbol = str(item.get("symbol_name") or "").lower()
            signals = set(item.get("lexical_matches") or []) | set(item.get("exact_identifier_matches") or [])
            symbol_signal_count = sum(1 for signal in signals if signal and signal.lower() in symbol)
            return (-symbol_signal_count, item.get("retrieval_rank", 10**9), item.get("start_line", 0))

        children.sort(key=child_order)
        chosen = []
        covered_roles, covered_signals = set(), set()
        for child in children:
            new_roles = _classify_evidence_roles(child, strict_downstream_helper).intersection(requested) - covered_roles
            child_signals = set(child.get("lexical_matches") or []) | set(child.get("exact_identifier_matches") or [])
            if new_roles or child_signals - covered_signals:
                chosen.append(child)
                covered_roles.update(_classify_evidence_roles(child, strict_downstream_helper).intersection(requested))
                covered_signals.update(child_signals)
            covered_ratio = len(parent_signals.intersection(covered_signals)) / max(1, len(parent_signals))
            required_ratio = 1.0 if parent_roles else 0.5
            if parent_roles.issubset(covered_roles) and covered_ratio >= required_ratio:
                break
        signal_coverage = len(parent_signals.intersection(covered_signals)) / max(1, len(parent_signals))
        minimum_signal_coverage = 1.0 if parent_roles else 0.5
        if not chosen or not parent_roles.issubset(covered_roles) or signal_coverage < minimum_signal_coverage:
            continue
        proposed = [item for item in result if item["chunk_id"] != parent["chunk_id"]]
        for child in chosen:
            if child["chunk_id"] not in {item["chunk_id"] for item in proposed}:
                proposed.append(child)
        if len(proposed) > top_k:
            continue
        result = proposed
        containment_replacements.append({"parent_chunk_id": parent["chunk_id"], "child_chunk_ids": [item["chunk_id"] for item in chosen]})

    result, policy_continuations = _preserve_split_policy_continuations(
        result, ranked, requested, top_k, strict_downstream_helper
    )
    result, locale_removed = _deduplicate_localized_resources(result)
    return result[:top_k], {
        "parent_child_replacements": containment_replacements,
        "parent_child_replacement_count": len(containment_replacements),
        "localized_resource_chunks_removed": locale_removed,
        "localized_resource_chunks_removed_count": len(locale_removed),
        "split_policy_continuations": policy_continuations,
        "split_policy_continuation_count": len(policy_continuations),
    }


def _preserve_split_policy_continuations(
    current: list[dict], ranked: list[dict], requested: set[str], top_k: int,
    strict_downstream_helper: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Keep an exact adjacent chunk only when it completes a split policy decision."""
    if "needs_authority_checks" not in requested:
        return list(current), []
    result = list(_dedupe_source_chunks(current))
    continuations = []
    ranked_by_path_start = {
        (str(item.get("file_path") or ""), int(item.get("start_line") or 0)): item for item in ranked
    }
    for first in list(result):
        if first.get("chunk_type") != "line_range_fallback":
            continue
        if "needs_authority_checks" not in _classify_evidence_roles(first, strict_downstream_helper):
            continue
        first_code = str(first.get("code_snippet") or "")
        if not _ends_inside_policy_decision(first_code):
            continue
        neighbor = ranked_by_path_start.get((str(first.get("file_path") or ""), int(first.get("end_line") or 0) + 1))
        if not neighbor or neighbor.get("chunk_type") != "line_range_fallback":
            continue
        if not _continues_policy_decision(str(neighbor.get("code_snippet") or "")):
            continue
        neighbor_roles = _classify_evidence_roles(neighbor, strict_downstream_helper)
        neighbor_tags = str(neighbor.get("security_tags") or "").lower()
        if "needs_authority_checks" not in neighbor_roles and "access_check" not in neighbor_tags:
            continue
        if neighbor["chunk_id"] in {item["chunk_id"] for item in result}:
            continue
        if len(result) >= top_k:
            replace_index = next((
                index for index in range(len(result) - 1, -1, -1)
                if not result[index].get("selected_file_match")
                and "needs_authority_checks" not in _classify_evidence_roles(result[index], strict_downstream_helper)
                and all(
                    any(
                        other_index != index and role in _classify_evidence_roles(other, strict_downstream_helper)
                        for other_index, other in enumerate(result)
                    )
                    for role in _classify_evidence_roles(result[index], strict_downstream_helper).intersection(requested)
                )
            ), None)
            if replace_index is None:
                continue
            result.pop(replace_index)
        result.append(neighbor)
        continuations.append({
            "first_chunk_id": first["chunk_id"],
            "continuation_chunk_id": neighbor["chunk_id"],
            "file_path": first.get("file_path"),
            "boundary": [first.get("end_line"), neighbor.get("start_line")],
            "reason": "adjacent chunk completes an unfinished authorization decision",
        })
    return _dedupe_source_chunks(result)[:top_k], continuations


def _ends_inside_policy_decision(code: str) -> bool:
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if not lines:
        return False
    decision_starts = [index for index, line in enumerate(lines) if re.match(
        r"^(?:allow|permit|deny|authorize)\b.*\b(?:if|when|unless)\b", line, re.IGNORECASE
    )]
    if not decision_starts:
        return False
    tail = "\n".join(lines[decision_starts[-1]:])
    if ";" in tail:
        return False
    # A policy decision that starts in this chunk but has no terminator is
    # structurally incomplete even when its parentheses happen to balance.
    return True


def _continues_policy_decision(code: str) -> bool:
    lines = [line.strip() for line in code.splitlines() if line.strip()]
    if not lines:
        return False
    opening = lines[0]
    starts_as_continuation = bool(re.match(r"^(?:&&|\|\||\band\b|\bor\b|\)|\()", opening, re.IGNORECASE))
    return starts_as_continuation and ";" in "\n".join(lines)


def _localized_resource_family(path: str) -> tuple[str, str, bool] | None:
    normalized = str(path or "").replace("\\", "/")
    match = re.match(r"^(.*?/res/)values(?P<qualifier>-[^/]+)?/(?P<name>[^/]+\.xml)$", normalized, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower(), match.group("name").lower(), not bool(match.group("qualifier"))


def _xml_named_resources(code: str) -> dict[str, str]:
    return {
        match.group("name"): match.group(0)
        for match in re.finditer(r"<(?P<tag>string|string-array|plurals|item)\b[^>]*\bname\s*=\s*['\"](?P<name>[^'\"]+)['\"][^>]*>.*?</(?P=tag)\s*>", str(code or ""), re.IGNORECASE | re.DOTALL)
    }


def _deduplicate_localized_resources(chunks: list[dict]) -> tuple[list[dict], list[str]]:
    families: dict[tuple[str, str], list[tuple[dict, bool]]] = {}
    for item in chunks:
        family = _localized_resource_family(item.get("file_path") or "")
        if family:
            families.setdefault(family[:2], []).append((item, family[2]))
    removed = set()
    for members in families.values():
        bases = [item for item, is_base in members if is_base]
        if len(bases) != 1:
            continue
        base = bases[0]
        base_resources = _xml_named_resources(base.get("code_snippet") or "")
        if not base_resources:
            continue
        base_keys = set(base_resources)
        for variant, is_base in members:
            if is_base:
                continue
            variant_resources = _xml_named_resources(variant.get("code_snippet") or "")
            if not variant_resources:
                continue
            unique = set(variant_resources) - base_keys
            unique_text = " ".join(f"{key} {variant_resources[key]}" for key in unique).lower()
            unique_keys_text = " ".join(unique).lower()
            query_signals = set(variant.get("lexical_matches") or []) | set(variant.get("exact_identifier_matches") or [])
            unique_relevant = any(signal and signal.lower() in unique_keys_text for signal in query_signals)
            unique_security = any(marker in unique_keys_text for marker in SECURITY_RESOURCE_MARKERS)
            if not unique_relevant and not unique_security:
                removed.add(variant["chunk_id"])
    return [item for item in chunks if item["chunk_id"] not in removed], sorted(removed)


JAVA_MAPPING_PATTERN = re.compile(
    r'@(RequestMapping|GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\s*(?:\((?P<args>[^)]*)\))?',
    re.IGNORECASE | re.DOTALL,
)


def _mapping_path(args: str | None) -> str:
    if not args:
        return ''
    single = re.search(r"'([^']*)'", args)
    double = re.search(chr(34) + r'([^' + chr(34) + r']*)' + chr(34), args)
    match = single or double
    return match.group(1) if match else ''


def _extract_java_class_route(code: str) -> str | None:
    match = re.search(
        r'@RequestMapping\s*(?:\((?P<args>[^)]*)\))?\s*(?:public\s+)?class\s+',
        code,
        re.IGNORECASE | re.DOTALL,
    )
    return _mapping_path(match.group('args')) if match else None


def _route_metadata(code: str, language: str, class_route: str | None) -> dict:
    if language.lower() != "java":
        return {"class_route": None, "method_route": None, "effective_route": None, "http_method": None, "class_route_state": "unavailable", "method_route_state": "unavailable", "resolution_status": "unresolved"}
    match = JAVA_MAPPING_PATTERN.search(code)
    method_route = _mapping_path(match.group("args")) if match else None
    annotation = match.group(1).lower() if match else ""
    http_method = {"getmapping": "GET", "postmapping": "POST", "putmapping": "PUT", "patchmapping": "PATCH", "deletemapping": "DELETE"}.get(annotation)
    class_state = "absent" if class_route is None else ("explicit_empty" if class_route == "" else "present")
    method_state = "absent" if not match else ("explicit_empty" if method_route == "" else "present")
    if class_route is None and method_route is None:
        effective_route = None
    elif class_route is None and method_route == "":
        effective_route = "/"
    elif not class_route:
        effective_route = method_route
    elif not method_route:
        effective_route = class_route
    else:
        effective_route = f"{class_route.rstrip('/')}/{method_route.lstrip('/')}"
    return {"class_route": class_route, "method_route": method_route, "effective_route": effective_route, "http_method": http_method, "class_route_state": class_state, "method_route_state": method_state, "resolution_status": "resolved" if effective_route is not None else "unresolved"}


ENUMERATION_TERMS = (
    "list endpoints", "enumerate endpoints", "every endpoint", "each endpoint", "all endpoints",
    "every operation", "each operation", "all operations", "all methods", "access-control matrix", "access control matrix",
)


def _has_enumeration_intent(query: str) -> bool:
    lowered = query.lower()
    if any(term in lowered for term in ENUMERATION_TERMS):
        return True
    if _has_manifest_component_enumeration_intent(query):
        return True
    if not re.search(r"\bwhich\b", lowered):
        return False
    plural_category = bool(re.search(
        r"\bwhich\s+(?:[a-z0-9_-]+\s+){0,3}(?:writes|mutations|operations|actions|fields|endpoints|updates|changes|edits)\b",
        lowered,
    ))
    distributive_evidence = bool(re.search(
        r"\beach\s+(?:restriction|operation|write|blocked\s+action)\b",
        lowered,
    ))
    broad_category = bool(re.search(r"\b(?:writes|mutations|operations|actions|fields|endpoints|updates|changes|edits)\b", lowered))
    return plural_category or (distributive_evidence and broad_category)


def _has_manifest_component_enumeration_intent(query: str) -> bool:
    if not has_manifest_component_intent(query):
        return False
    lowered = query.lower()
    plural_family = bool(re.search(
        r"\b(?:components|activities|services|receivers|broadcast\s+receivers|providers|"
        r"content\s+providers|activity\s+aliases)\b",
        lowered,
    ))
    explicit_list = bool(re.search(r"\b(?:list|enumerate|all|every|each)\b", lowered))
    bounded_question = bool(re.search(r"^\s*which\b", lowered)) or bool(re.search(
        r"^\s*what\s+(?:android\s+|manifest\s+)?components\b.*\b(?:are|can|have|require)",
        lowered,
    ))
    return plural_family and (explicit_list or bounded_question)


def _selected_chunk_relevant(row: dict, enumeration_intent: bool) -> bool:
    tags = {tag.strip().lower() for tag in (row.get("security_tags") or "").split(",") if tag.strip()}
    chunk_type = (row.get("chunk_type") or "").lower()
    code = (row.get("code") or "").lower()
    is_endpoint = (
        chunk_type == "route_handler"
        or (chunk_type in {"method", "function", "async_function"} and any(tag in HTTP_VERB_TAGS for tag in tags))
        or any(marker in code for marker in (
            "@getmapping", "@postmapping", "@putmapping", "@patchmapping",
            "@deletemapping", "@requestmapping", "@app.get", "@app.post",
            "router.get(", "router.post(",
        ))
    )
    is_security_relevant = bool(tags) or any(
        marker in code for marker in ("preauthorize", "hasauthority", "hasrole", "authorize", "permission")
    )
    if enumeration_intent:
        return is_endpoint or is_security_relevant
    return is_security_relevant


def _merge_selected_file_coverage(
    global_candidates: list[dict],
    selected_candidates: list[dict],
    top_k: int,
    minimum: int,
    maximum: int,
    enumeration_intent: bool,
) -> tuple[list[dict], int]:
    if not selected_candidates or top_k <= 0:
        unique = _dedupe_source_chunks(global_candidates)
        return unique[:top_k], len(global_candidates) - len(unique)

    cap = min(maximum, top_k)
    reserve = min(minimum if enumeration_intent else 1, cap)
    diversified = []
    seen_symbols: set[str] = set()
    for item in selected_candidates:
        symbol = str(item.get("symbol_name") or item["chunk_id"]).lower()
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        diversified.append(item)
        if len(diversified) >= cap:
            break

    selected = diversified[:reserve]
    selected_ids = {item["chunk_id"] for item in selected}
    selected_symbols = {str(item.get("symbol_name") or item["chunk_id"]).lower() for item in selected}
    result = list(selected)
    selected_count = len(selected)
    for item in global_candidates:
        chunk_id = item["chunk_id"]
        if chunk_id in selected_ids:
            continue
        symbol = str(item.get("symbol_name") or chunk_id).lower()
        if item.get("selected_file_match") and symbol in selected_symbols:
            continue
        if item.get("selected_file_match") and selected_count >= cap:
            continue
        result.append(item)
        selected_ids.add(chunk_id)
        if item.get("selected_file_match"):
            selected_count += 1
            selected_symbols.add(symbol)
        if len(result) >= top_k:
            break
    result = sorted(result, key=lambda item: item.get("final_score", 0.0), reverse=True)
    unique_count = len(_dedupe_source_chunks([*selected_candidates, *global_candidates]))
    return result[:top_k], len(selected_candidates) + len(global_candidates) - unique_count


def _dedupe_source_chunks(chunks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        result.append(chunk)
    return result


def _normalize_selected_file_path(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("\\", "/").strip().strip("/")
    return cleaned.lower() or None


def _dedupe_wiki_chunks(chunks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for chunk in chunks:
        key = str(chunk.get("id") or chunk.get("wiki_page_id") or len(result))
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


def retrieve_evidence(project_id: str, question: str, module_id: str | None = None, limit: int = 8) -> list[dict]:
    if module_id:
        return _retrieve_evidence_legacy(project_id, question, module_id, limit)
    with db() as connection:
        return retrieve_evidence_package(project_id, question, limit, connection)["source_chunks"]


def _retrieve_evidence_legacy(project_id: str, question: str, module_id: str | None = None, limit: int = 8) -> list[dict]:
    terms = [term.lower() for term in re_split(question) if len(term) > 2]
    vector_hits = vector_query(project_id, question, limit=limit, source_type="code")
    vector_chunk_ids = [hit.metadata.get("chunk_id") for hit in vector_hits if hit.metadata.get("chunk_id")]
    query = """
        SELECT c.*, f.file_path, f.language
        FROM code_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE c.project_id = ?
    """
    params: list[str] = [project_id]
    if module_id:
        query += " AND f.file_path = ?"
        params.append(module_id)
    with db() as connection:
        rows = connection.execute(query, params).fetchall()
    scored = []
    for row in rows:
        tags = row["security_tags"] or ""
        haystack = f"{row['file_path']} {row['symbol_name'] or ''} {tags} {row['code']}".lower()
        score = sum(2 for term in terms if term in haystack)
        if row["id"] in vector_chunk_ids:
            score += 8 - vector_chunk_ids.index(row["id"])
        if tags:
            score += 3
        if module_id and row["file_path"] == module_id:
            score += 4
        if score > 0:
            scored.append((score, row_to_dict(row)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    evidence = []
    for _, row in scored[:limit]:
        evidence.append(
            {
                "chunk_id": row["id"],
                "file_path": row["file_path"],
                "symbol_name": row["symbol_name"],
                "class_name": row["class_name"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "language": row["language"],
                "security_tags": row["security_tags"],
                "code_snippet": row["code"],
                "critical_lines": critical_lines(row["code"], row["start_line"]),
            }
        )
    return evidence


def retrieve_wiki_context(project_id: str, question: str, module_id: str | None = None, limit: int = 5) -> list[dict]:
    if limit <= 0:
        return []
    candidate_limit = max(limit * 5, limit)
    hits = vector_query(project_id, question, limit=candidate_limit, source_type="wiki", module_id=module_id)
    if not hits and module_id:
        hits = vector_query(project_id, question, limit=candidate_limit, source_type="wiki")
    candidate_count = len(hits)
    requested_roles = _extract_evidence_roles(question)
    context = []
    for hit in hits:
        signals = _query_match_signals(question, hit.document or "", [hit.metadata.get("section_title") or ""])
        wiki_roles = _classify_evidence_roles({"code_snippet": hit.document or ""})
        role_relevance = min(1.0, len(wiki_roles.intersection(requested_roles)) / max(1, len(requested_roles)))
        lowered_document = (hit.document or "").lower()
        absence_markers = sum(lowered_document.count(marker) for marker in ("not available", "unavailable", "does not contain", "no explicit", "not verified"))
        base_relevance = 0.0 if hit.distance is None else max(0.0, min(1.0, 1.0 - float(hit.distance)))
        hybrid_relevance = base_relevance + 0.25 * signals["lexical_relevance"] + 0.25 * role_relevance - min(0.3, absence_markers * 0.08)
        context.append(
            {
                "id": hit.id,
                "chunk_id": hit.id,
                "wiki_id": hit.metadata.get("wiki_page_id"),
                "wiki_page_id": hit.metadata.get("wiki_page_id"),
                "module_id": hit.metadata.get("module_id"),
                "title": hit.metadata.get("title"),
                "section_title": hit.metadata.get("section_title"),
                "section": hit.metadata.get("section_title"),
                "source_focus": hit.metadata.get("module_id"),
                "chunk_index": hit.metadata.get("chunk_index"),
                "content": hit.document,
                "source_type": hit.source_type,
                "distance": hit.distance,
                "relevance": base_relevance,
                "wiki_lexical_relevance": signals["lexical_relevance"],
                "wiki_role_relevance": role_relevance,
                "wiki_absence_penalty": min(0.3, absence_markers * 0.08),
                "wiki_hybrid_relevance": round(hybrid_relevance, 4),
                "candidate_wiki_chunk_count": candidate_count,
            }
        )
    ranked = sorted(context, key=lambda item: item["wiki_hybrid_relevance"], reverse=True)
    for rank, item in enumerate(ranked, 1):
        item["retrieval_rank"] = rank
    return _dedupe_wiki_chunks(ranked)[:limit]


CRITICAL_KEYWORDS = {
    "checkPermission",
    "enforcePermission",
    "hasPermission",
    "authorize",
    "authorization",
    "SecurityException",
    "AccessDenied",
    "Forbidden",
    "getCallingUid",
    "getCallingUserId",
    "Binder.getCallingUid",
    "isAccountManagedByCaller",
    "hasSignatureCapability",
    "role",
    "authority",
    "hasRole",
    "hasAuthority",
    "authenticated",
    "requestMatchers",
    "antMatchers",
    "permitAll",
    "denyAll",
}


def critical_lines(code: str, start_line: int) -> list[int]:
    lines = []
    for offset, line in enumerate(code.splitlines()):
        lower = line.lower()
        if any(keyword.lower() in lower for keyword in CRITICAL_KEYWORDS):
            lines.append(start_line + offset)
    return lines


def _clone_repository(repo_url: str, repo_path: Path) -> None:
    settings = get_settings()
    repo_url = normalize_source_url(repo_url) or ""
    if repo_path.exists() and any(repo_path.iterdir()):
        shutil.rmtree(repo_path)
    repo_path.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--depth", "1", repo_url, str(repo_path)]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.git_clone_timeout_seconds,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        proxy_values = [os.environ.get(name, "") for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")]
        if any("127.0.0.1:9" in value or "localhost:9" in value for value in proxy_values):
            retry_env = os.environ.copy()
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                retry_env.pop(name, None)
            try:
                subprocess.run(
                    _openssl_git_command(command),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=settings.git_clone_timeout_seconds,
                    env=retry_env,
                )
                return
            except subprocess.CalledProcessError as retry_exc:
                raise RuntimeError(_git_error_message(retry_exc, "Git clone failed after retrying without the local proxy and with OpenSSL.")) from retry_exc
        if "schannel" in detail.lower():
            try:
                subprocess.run(
                    _openssl_git_command(command),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=settings.git_clone_timeout_seconds,
                )
                return
            except subprocess.CalledProcessError as retry_exc:
                raise RuntimeError(_git_error_message(retry_exc, "Git clone failed after retrying with OpenSSL.")) from retry_exc
        raise RuntimeError(_git_error_message(exc, "Git clone failed.")) from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"Git clone timed out after {settings.git_clone_timeout_seconds} seconds. Use a smaller repository or selected subfolder/package."
        ) from exc


def _git_error_message(exc: subprocess.CalledProcessError, prefix: str) -> str:
    detail = (exc.stderr or exc.stdout or str(exc)).strip()
    if detail:
        return f"{prefix} {detail}"
    return f"{prefix} git exited with status {exc.returncode}."


def _openssl_git_command(command: list[str]) -> list[str]:
    if len(command) >= 2 and command[0] == "git":
        return ["git", "-c", "http.sslBackend=openssl", *command[1:]]
    return command


def _directory_size_mb(path: Path) -> float:
    total_bytes = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total_bytes += item.stat().st_size
        except OSError:
            continue
    return total_bytes / (1024 * 1024)


def _safe_extract_zip(archive: zipfile.ZipFile, target: Path) -> None:
    target_resolved = target.resolve()
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if target_resolved not in destination.parents and destination != target_resolved:
            raise ValueError(f"Unsafe ZIP path: {member.filename}")
    archive.extractall(target)


def _commit_hash(repo_path: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.SubprocessError:
        return None


def re_split(text: str) -> list[str]:
    return [part for part in "".join(char if char.isalnum() else " " for char in text).split() if part]


def evidence_to_prompt(evidence: list[dict]) -> str:
    if not evidence:
        return "No source evidence was retrieved."
    blocks = []
    for index, item in enumerate(evidence, start=1):
        blocks.append(
            "\n".join(
                [
                    f"Evidence {index}",
                    f"Evidence ID: {item['chunk_id']}",
                    f"File: {item['file_path']}",
                    f"Symbol: {item.get('symbol_name') or 'unknown'}",
                    f"Lines: {item['start_line']}-{item['end_line']}",
                    f"HTTP Method: {item.get('http_method') or ''}",
                    f"Class Route State: {item.get('class_route_state') or 'unavailable'}",
                    f"Class Route: {item.get('class_route') if item.get('class_route') is not None else 'unavailable'}",
                    f"Method Route State: {item.get('method_route_state') or 'unavailable'}",
                    f"Method Route: {item.get('method_route') if item.get('method_route') is not None else 'unavailable'}",
                    f"Effective Route: {item.get('effective_route') if item.get('effective_route') is not None else 'unavailable'}",
                    f"Route Resolution Status: {item.get('route_resolution_status') or 'unresolved'}",
                    f"Retrieval Rank: {item.get('retrieval_rank') or 'unavailable'}",
                    f"Prompt Position: {item.get('prompt_position') or index}",
                    f"Evidence Priority: {item.get('evidence_priority_class') or 'optional_context'}",
                    f"Security Tags: {item.get('security_tags') or ''}",
                    "Code:",
                    item["code_snippet"],
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def compact_evidence_to_prompt(evidence: list[dict]) -> str:
    """Deterministic provider envelope; full retrieval diagnostics remain on evidence objects."""
    if not evidence:
        return "No source evidence was retrieved."
    blocks = []
    for index, item in enumerate(evidence, start=1):
        lines = [
            f"[E{index}]",
            f"FILE: {item['file_path']}",
        ]
        if item.get("class_name"):
            lines.append(f"TYPE: {item['class_name']}")
        lines.extend([
            f"SYMBOL: {item.get('symbol_name') or 'unknown'}",
            f"LINES: {item['start_line']}-{item['end_line']}",
            f"ROLE: {item.get('evidence_priority_class') or 'optional_context'}",
        ])
        if item.get("route_resolution_status") == "resolved" and item.get("effective_route") is not None:
            method = f"{item.get('http_method')} " if item.get("http_method") else ""
            lines.append(f"ROUTE: {method}{item['effective_route']}")
        lines.extend(["SOURCE:", item["code_snippet"]])
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


EXISTENCE_SEARCH_LIMITATION = (
    "Search results cover indexed source chunks only. A not_found result does not prove absence from "
    "unindexed files, generated code, runtime-only configuration, "
    "encrypted configuration, or concepts expressed in a way the search could not recognize."
)


def repository_existence_to_prompt(searches: list[dict]) -> str:
    """Serialize system-generated repository search metadata separately from source evidence."""
    if not searches:
        return ""
    blocks = []
    for index, search in enumerate(searches, 1):
        exact_hits = search.get("exact_symbol_hits") or []
        lexical_hits = search.get("lexical_hits") or []
        strong_semantic_hits = [hit for hit in (search.get("semantic_hits") or []) if float(hit.get("similarity") or 0) >= 0.65]
        references = []
        seen = set()
        for hit in [*exact_hits, *lexical_hits, *strong_semantic_hits]:
            chunk_id = hit.get("chunk_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            references.append({
                "chunk_id": chunk_id,
                "file_path": hit.get("file_path"),
                "symbol_name": hit.get("symbol_name"),
            })
        result = str(search.get("existence_result") or "uncertain")
        lines = [
            f"[X{index}] Repository-wide existence check",
            f"CONCEPT: {search.get('concept_searched') or ''}",
            f"SCOPE: {search.get('search_scope') or ''}",
            "SEARCH TERMS: " + json.dumps(search.get("search_terms") or [], ensure_ascii=False, separators=(",", ":")),
            f"RESULT: {result}",
            f"SCANNED CHUNKS: {int(search.get('scanned_chunk_count') or 0)}",
            f"CANDIDATE COUNT: {int(search.get('candidate_count') or 0)}",
            f"EXACT SYMBOL HITS: {len(exact_hits)}",
            f"LEXICAL HITS: {len(lexical_hits)}",
            f"STRONG SEMANTIC HITS: {len(strong_semantic_hits)}",
            "MATCHING SOURCE REFERENCES: " + json.dumps(references, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ]
        if result == "uncertain":
            lines.append("INTERPRETATION: Possible semantic matches were found; the search cannot safely conclude that the concept is present or absent.")
        lines.append(f"LIMITATION: {EXISTENCE_SEARCH_LIMITATION}")
        blocks.append("\n".join(lines))
    return "[REPOSITORY EXISTENCE CHECKS]\n\n" + "\n\n---\n\n".join(blocks)
