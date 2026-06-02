# READ SUMMARY: This module creates, imports, indexes, deletes, and retrieves project evidence from SQLite and ChromaDB.
# CHANGED: Added a shared evidence-package retrieval path so Ask and Compare use identical source and wiki retrieval with query-time re-scoring.
import os
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
from app.services.vector_index import clear_project, index_code_chunk, query as vector_query, rescore_chunks


ANDROID_CASE_STUDIES = [
    {"id": "account-manager-service", "name": "AccountManagerService", "hint": "Link an Android source tree or GitHub URL containing AccountManagerService.java."},
    {"id": "service-manager", "name": "ServiceManager", "hint": "Link Android framework/native service manager sources."},
    {"id": "binder-token-handling", "name": "Binder Token Handling", "hint": "Link Android Binder-related source package."},
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
    source_url = payload.repo_url if payload.source_type == "github" else payload.android_source_url
    subfolder_path = normalize_subfolder_path(payload.subfolder_path)
    timestamp = now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO projects (id, name, source_type, repo_url, local_path, subfolder_path, commit_hash, status, status_message, security_goal, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.name,
                payload.source_type,
                source_url,
                str(repo_path.resolve()),
                subfolder_path,
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


def discover_security_modules(project_id: str, security_goal: str = "") -> list[dict]:
    goal_terms = {term.lower() for term in security_goal.replace("-", " ").split() if len(term) > 2}
    with db() as connection:
        rows = connection.execute(
            """
            SELECT f.file_path, f.language, COUNT(c.id) AS chunk_count, GROUP_CONCAT(c.security_tags) AS tags, GROUP_CONCAT(c.symbol_name) AS symbols
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
        text = f"{row['file_path']} {row['symbols'] or ''} {' '.join(tags)}"
        keyword_hits = sum(1 for term in goal_terms if term in text.lower())
        if tags or keyword_hits:
            confidence = confidence_for_tags(tags)
            if keyword_hits >= 2 and confidence != "High":
                confidence = "High"
            symbols = sorted({symbol for symbol in (row["symbols"] or "").split(",") if symbol})
            reason = "contains security keywords/tags"
            if keyword_hits:
                reason += f" and matches audit goal terms ({keyword_hits})"
            candidates.append(
                {
                    "module_path": row["file_path"],
                    "language": row["language"],
                    "reason": reason,
                    "confidence": confidence,
                    "security_tags": tags,
                    "matching_symbols": symbols[:12],
                    "matching_chunk_count": row["chunk_count"],
                    "score": len(tags) * 3 + keyword_hits,
                }
            )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:25]


def retrieve_evidence_package(project_id: str, query: str, top_k: int, db_conn: sqlite3.Connection) -> dict:
    # ChromaDB returns distances where lower is better; vector_index.query converts each distance to hit.distance.
    # Re-ranking now happens after ChromaDB query via rescore_chunks.
    # Available metadata for code chunks: source_type, chunk_id, project_id, file_path, symbol_name, start_line, end_line, language, security_tags, tags, chunk_type.
    # Current top_k is supplied by the caller; Ask and Compare pass the same value.
    # File-type weighting is applied here through vector_index.rescore_chunks, not during indexing.
    terms = [term.lower() for term in re_split(query) if len(term) > 2]
    vector_hits = vector_query(project_id, query, limit=top_k, source_type="code")
    vector_rank = {hit.metadata.get("chunk_id"): index for index, hit in enumerate(vector_hits) if hit.metadata.get("chunk_id")}
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
    candidates = []
    for row in rows:
        row_dict = row_to_dict(row)
        tags = row_dict.get("security_tags") or ""
        haystack = f"{row_dict['file_path']} {row_dict.get('symbol_name') or ''} {tags} {row_dict['code']}".lower()
        lexical_score = sum(2 for term in terms if term in haystack)
        if row_dict["id"] in vector_rank:
            lexical_score += 8 - vector_rank[row_dict["id"]]
        if tags:
            lexical_score += 3
        if lexical_score <= 0:
            continue
        base_similarity = vector_similarity.get(row_dict["id"], min(1.0, lexical_score / 10.0))
        candidates.append(
            {
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
                "code_snippet": row_dict["code"],
                "critical_lines": critical_lines(row_dict["code"], row_dict["start_line"]),
                "base_similarity": base_similarity,
            }
        )
    source_chunks = rescore_chunks(candidates)[:top_k]
    wiki_chunks = retrieve_wiki_context(project_id, query, limit=min(5, top_k))
    chunk_ids = [chunk["chunk_id"] for chunk in source_chunks] + [chunk["id"] for chunk in wiki_chunks if chunk.get("id")]
    retrieval_log = f"[Retrieval] query={query}, top_k={top_k}, ids={chunk_ids}"
    print(f"[Retrieval] query='{query}', top_k={top_k}, chunk_ids={[c['id'] for c in source_chunks]}")
    return {
        "source_chunks": source_chunks,
        "wiki_chunks": wiki_chunks,
        "chunk_ids": chunk_ids,
        "retrieval_log": retrieval_log,
    }


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
    hits = vector_query(project_id, question, limit=limit, source_type="wiki", module_id=module_id)
    if not hits and module_id:
        hits = vector_query(project_id, question, limit=limit, source_type="wiki")
    context = []
    for hit in hits:
        context.append(
            {
                "id": hit.id,
                "wiki_page_id": hit.metadata.get("wiki_page_id"),
                "module_id": hit.metadata.get("module_id"),
                "title": hit.metadata.get("title"),
                "section_title": hit.metadata.get("section_title"),
                "chunk_index": hit.metadata.get("chunk_index"),
                "content": hit.document,
            }
        )
    return context


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
                    f"Security Tags: {item.get('security_tags') or ''}",
                    "Code:",
                    item["code_snippet"],
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)
