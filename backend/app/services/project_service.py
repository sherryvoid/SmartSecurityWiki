import shutil
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
from app.services.vector_index import clear_project, index_code_chunk, query as vector_query


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
    timestamp = now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO projects (id, name, source_type, repo_url, local_path, commit_hash, status, status_message, security_goal, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.name,
                payload.source_type,
                source_url,
                str(repo_path),
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
    repo_path = Path(project["local_path"])
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
        update_status(project_id, "fetching", "Fetching repository...")
        if project["source_type"] in {"github", "android"} and project["repo_url"]:
            _clone_repository(project["repo_url"], Path(project["local_path"]))
            commit_hash = _commit_hash(Path(project["local_path"]))
            with db() as connection:
                connection.execute("UPDATE projects SET commit_hash = ? WHERE id = ?", (commit_hash, project_id))
        update_status(project_id, "fetched", "Repository fetched.")
        index_project(project_id)
    except Exception as exc:
        update_status(project_id, "failed", f"Import failed: {exc}")


def index_project(project_id: str) -> None:
    project = get_project(project_id)
    if not project:
        return
    repo_root = Path(project["local_path"])
    update_status(project_id, "indexing", "Filtering files...")
    with db() as connection:
        connection.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
        connection.execute("DELETE FROM code_chunks WHERE project_id = ?", (project_id,))
    clear_project(project_id)
    for file_path in repo_root.rglob("*"):
        if not file_path.is_file() or not is_relevant_file(file_path):
            continue
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
        update_status(project_id, "indexing", f"Extracting symbols from {relative}")
        chunks = chunk_source(relative, language, text)
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
            connection.execute("UPDATE files SET is_indexed = 1 WHERE id = ?", (file_id,))
    update_status(project_id, "indexed", "Ready.")


def list_projects() -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [row_to_dict(row) for row in rows]


def get_project(project_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return row_to_dict(row)


def update_status(project_id: str, status: str, message: str) -> None:
    with db() as connection:
        connection.execute(
            "UPDATE projects SET status = ?, status_message = ?, updated_at = ? WHERE id = ?",
            (status, message, now(), project_id),
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


def retrieve_evidence(project_id: str, question: str, module_id: str | None = None, limit: int = 8) -> list[dict]:
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
    if repo_path.exists() and any(repo_path.iterdir()):
        shutil.rmtree(repo_path)
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", repo_url, str(repo_path)], check=True, capture_output=True, text=True)


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
