# READ SUMMARY: This module orchestrates wiki generation, evidence-backed chat, model comparison, verification, scoring, and exports.
# CHANGED: Added selected-file preference support, stronger wiki evidence disclaimers, full wiki chunk-id validation, Ollama model selection support, and clearer HTML report export wording.
import csv
import hashlib
import html
import io
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.database import db
from app.db.schemas import ChatAnswer, ChatRequest, CompareRequest, DISPLAY_STATUS_MAP, EvaluationScoreRequest, SecurityWikiSchema, VerificationRequest, WikiGenerateRequest, display_status_for
from app.services.llm import active_provider_names, context_incompatible_response, generate_structured_security_wiki_diagnostic, is_provider_available, prompt_fit_preflight, provider_for, sanitize_diagnostic_text, security_wiki_system_prompt
from app.services.methodology import evaluation_configuration, freeze_wiki_context, persist_formal_run, render_structured_answer_payload, validate_model_references
from app.services.project_service import compact_evidence_to_prompt, evidence_to_prompt, now, repository_existence_to_prompt, retrieve_evidence, retrieve_evidence_package, retrieve_wiki_context
from app.services.vector_index import code_chunk_exists, delete_wiki_page_vectors, index_wiki_page
from app.services.usage_service import measure_prompt_components, normalize_usage, persist_usage


logger = logging.getLogger(__name__)

EVIDENCE_DISCLAIMER = "Generated Security Wiki content is used as orientation. Source-code and configuration chunks are the primary evidence."


def _selected_provider_available(provider: str, model: str) -> bool:
    """Check an exact selection while remaining compatible with simple test doubles."""
    try:
        return is_provider_available(provider, model)
    except TypeError:
        return is_provider_available(provider)


SYSTEM_PROMPT = """You are a security audit assistant.

You must answer only using the provided evidence.
Do not invent files, functions, line numbers, permissions, or call chains.
Every claim must be tied to a provided evidence block.
If the evidence is insufficient, say: "Not verified from the available evidence."
Wiki context is orientation only. It is not source-code proof.

Return:
1. Direct answer
2. Evidence blocks with file path and line range
3. Relevant code snippet
4. Explanation of access-control logic
5. Helper chain if supported by evidence
6. Missing evidence / needs review
"""

CHAT_JSON_PROMPT = """You are a security audit assistant.

You must answer only using the provided source-code evidence.
Do not invent files, functions, line numbers, permissions, or call chains.
Wiki context is orientation only. It is not source-code proof.
Reference only retrieved aliases E1, E2, ... in evidence_refs.
Report ROUTE exactly when supplied.
Do not infer conventional framework prefixes or likely paths.
If the source-code evidence is insufficient, say so in answer and limitations.

Return only valid JSON with this shape:
{
  "answer": "string",
  "confidence": "high|medium|low",
  "access_control_summary": "string or null",
  "evidence_refs": ["retrieved evidence id"],
  "helper_chain": ["string"],
  "limitations": ["string"],
  "needs_review": true
}
"""

GPT51_CONCISE_PRESENTATION_PROMPT = """GPT-5.1 presentation configuration: gpt51-concise-v1.
Answer completely but concisely. Prefer a compact evidence-grounded explanation and normally finish well below the output-token ceiling once the answer is complete.
Do not repeat the same fact in multiple fields or sections. Do not reproduce long source-code blocks unless exact syntax is security-relevant or the question asks for exact code; summarize code in prose while retaining important identifiers, authorities, routes, method names, line ranges, and evidence references.
For vertical traces, state one compact repository execution chain (for example controller -> authorization -> service -> downstream implementation), then explain only material details. For enumeration questions, prefer one compact table or bullet matrix and do not repeat it in prose.
Keep access_control_summary to 1-3 sentences and do not restate the full answer. helper_chain must contain only actual repository implementation/helper relationships, never analysis steps such as parsing, inspecting, cross-referencing, or evaluating. Limit limitations to material evidence gaps that affect the conclusion; omit generic speculative caveats when supplied evidence proves the point.
Preserve the required JSON schema and every technically important, source-supported fact. Do not change grounding, confidence, evidence_refs, or needs_review rules."""


def presentation_configuration(provider: str | None, model: str | None) -> dict:
    """Identify the answer-style contract without changing generation parameters."""
    if (provider or "").lower() == "openrouter" and (model or "").lower() in {"openai/gpt-5.1", "gpt-5.1"}:
        return {"presentation_prompt_version": get_settings().gpt51_presentation_version}
    return {}

SIMPLE_PROMPT_TEMPLATE = """You are a security code auditor.
Answer using ONLY the source code evidence below.
If the evidence does not contain the answer, say exactly:
"The evidence does not show this."
Do NOT invent file paths, line numbers, or class names.
Report ROUTE exactly when supplied.
Do not infer conventional framework prefixes or likely paths.

SOURCE CODE EVIDENCE:
{evidence_blocks}
{existence_section}

GENERATED WIKI CONTEXT (orientation only; never use as proof):
{wiki_context_blocks}

QUESTION: {query}

Write your answer clearly. Then on separate lines at the end:
FILE: <exact file_path from evidence or "unknown">
LINE: <start line from evidence or "unknown">
CONFIDENCE: high / medium / low
LIMITATIONS: <what is missing from the evidence>
"""


def wiki_context_to_prompt(wiki_context: list[dict]) -> str:
    if not wiki_context:
        return "No generated wiki context was retrieved."
    blocks = []
    for index, item in enumerate(wiki_context, start=1):
        blocks.append(
            "\n".join(
                [
                    f"Wiki Context {index}",
                    f"Title: {item.get('title') or ''}",
                    f"Section: {item.get('section_title') or ''}",
                    f"Selected Module: {item.get('module_id') or ''}",
                    "Content:",
                    item.get("content") or "",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


async def generate_wiki(project_id: str, request: WikiGenerateRequest) -> dict:
    execution_id = str(uuid.uuid4())
    started_at = now()
    started_perf = time.perf_counter()
    evidence = retrieve_evidence(project_id, request.module_path, request.module_path, limit=12)
    chunk_lookup = _wiki_chunk_lookup(project_id, request.module_path)
    evidence_prompt = evidence_to_prompt(evidence)
    chunk_lookup_prompt = "\n".join(f"{symbol}: {chunk_id}" for symbol, chunk_id in chunk_lookup.items())
    provider, default_model = provider_for(request.provider)
    model = request.model or default_model
    messages = [
        {"role": "system", "content": security_wiki_system_prompt()},
        {
            "role": "user",
            "content": (
                "Generate a structured Security Wiki for the selected target module using only this evidence. "
                "Populate module_overview, entry_points, access_control_matrix, vertical_helpers, "
                "requirement_traces, and limitations. Use chunk_id only when it appears in the retrieved evidence. "
                "Use 'authority-based method authorization' for hasAuthority/hasAnyAuthority unless explicit role hierarchy evidence exists. "
                "Never imply that one authority includes another without hierarchy evidence. Distinguish authorization helpers, "
                "business-logic dependencies, and downstream execution methods. Do not claim a static call chain because no static call graph is available. "
                "If evidence is missing, state that in limitations.\n\n"
                f"Target module: {request.module_path}\n\n"
                f"Known symbol-to-chunk IDs:\n{chunk_lookup_prompt or 'No indexed symbol chunk IDs found.'}\n\n"
                f"{evidence_prompt}"
            ),
        },
    ]
    wiki, raw_response, validation_status, provider_diagnostics = await generate_structured_security_wiki_diagnostic(provider, messages, model)
    source_package = _freeze_shared_evidence_package(evidence)
    empty_wiki_package = freeze_wiki_context([])
    wiki_prompt_composition = measure_prompt_components(messages, request.module_path, evidence, [], wiki_context_to_prompt)
    stored = validation_status in {"wiki_completed", "wiki_completed_with_warnings"}
    page_id = None
    if stored:
        _fill_missing_wiki_chunk_ids(wiki, chunk_lookup)
        validate_wiki_chunk_ids(project_id, wiki)
        _enrich_wiki_references(project_id, wiki)
        wiki_json = wiki.model_dump_json()
        content_markdown = render_wiki_to_markdown(wiki)
        page_id = str(uuid.uuid4())
        timestamp = now()
        with db() as connection:
            connection.execute(
                """INSERT INTO wiki_pages (id, project_id, module_id, title, slug, content_markdown, wiki_schema_version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (page_id, project_id, request.module_path, "Security Wiki", "security-wiki", wiki_json, "1.0", timestamp, timestamp),
            )
        index_wiki_page(project_id, page_id, request.module_path, "Security Wiki", content_markdown)
    else:
        content_markdown = ""
    failed_draft = raw_response if raw_response and not stored else None
    execution = _execution_details(
        execution_id, started_at, "wiki", validation_status, request.module_path, request.module_path,
        request.module_path, 12, evidence, [], request.provider, model,
        int((time.perf_counter() - started_perf) * 1000),
        provider_diagnostics or {"processing": {"response_received": bool(raw_response), "raw_response_length": len(raw_response),
         "think_tags_removed": False, "parser": "security_wiki_json", "parse_status": validation_status,
         "schema_validation_status": validation_status, "evidence_validation_status": "completed"}},
        sanitize_diagnostic_text(raw_response, get_settings().diagnostic_raw_response_max_chars) if request.provider == "ollama" else None,
    )
    usage = persist_usage(execution_id=execution_id, run_id=execution_id, project_id=project_id, operation="wiki", provider=request.provider, model=model, normalized=normalize_usage(request.provider, provider_diagnostics.get("usage", {})), duration_ms=int((time.perf_counter() - started_perf) * 1000), composition=wiki_prompt_composition, supplied_source=evidence, cited_source=evidence, wiki=[], source_hash=source_package["shared_evidence_hash"], wiki_hash=empty_wiki_package["shared_wiki_context_hash"], status=validation_status)
    return {
        "wiki_page_id": page_id,
        "content_markdown": content_markdown,
        "evidence": evidence,
        "provider": request.provider,
        "model": model,
        "validation_status": validation_status,
        "display_status": display_status_for(validation_status),
        "stored": stored,
        "failed_draft": sanitize_diagnostic_text(failed_draft, get_settings().diagnostic_raw_response_max_chars) if failed_draft else None,
        "raw_model_response": execution["provider"].get("sanitized_raw_response"),
        "execution": execution,
        "usage": usage,
    }


def list_wiki_pages(project_id: str) -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM wiki_pages WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)).fetchall()
    pages = []
    for row in rows:
        page = dict(row)
        page["content_markdown"] = wiki_storage_to_markdown(page.get("content_markdown") or "")
        pages.append(page)
    return pages


def _wiki_chunk_lookup(project_id: str, file_path: str) -> dict[str, str]:
    with db() as connection:
        rows = connection.execute(
            """
            SELECT c.symbol_name, c.id
            FROM code_chunks c
            JOIN files f ON f.id = c.file_id
            WHERE c.project_id = ? AND f.file_path = ? AND c.symbol_name IS NOT NULL
            """,
            (project_id, file_path),
        ).fetchall()
    return {row["symbol_name"]: row["id"] for row in rows if row["symbol_name"]}


def _fill_missing_wiki_chunk_ids(wiki: SecurityWikiSchema, chunk_lookup: dict[str, str]) -> None:
    if not chunk_lookup:
        return
    normalized_lookup = {name.lower(): chunk_id for name, chunk_id in chunk_lookup.items()}
    for item in wiki.entry_points:
        if not item.chunk_id:
            item.chunk_id = _lookup_chunk_id(item.name, normalized_lookup)
    for item in wiki.access_control_matrix:
        if not item.chunk_id:
            item.chunk_id = _lookup_chunk_id(item.caller, normalized_lookup) or _lookup_chunk_id(item.permission_check, normalized_lookup)
    for item in wiki.vertical_helpers:
        if not item.chunk_id:
            item.chunk_id = _lookup_chunk_id(item.name, normalized_lookup)


def _lookup_chunk_id(value: str | None, chunk_lookup: dict[str, str]) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    candidates = [normalized, normalized.split("(", 1)[0].strip(), normalized.rsplit(".", 1)[-1]]
    return next((chunk_lookup[candidate] for candidate in candidates if candidate in chunk_lookup), None)


def delete_wiki_page(project_id: str, wiki_page_id: str) -> dict:
    with db() as connection:
        row = connection.execute(
            "SELECT id FROM wiki_pages WHERE id = ? AND project_id = ?",
            (wiki_page_id, project_id),
        ).fetchone()
    if not row:
        return {"deleted": False, "wiki_page_id": wiki_page_id, "message": "Wiki page not found."}
    if not delete_wiki_page_vectors(project_id, wiki_page_id):
        return {"deleted": False, "wiki_page_id": wiki_page_id, "message": "Wiki vectors could not be deleted."}
    with db() as connection:
        connection.execute("DELETE FROM verifications WHERE target_type = 'wiki_page' AND target_id = ?", (wiki_page_id,))
        connection.execute("DELETE FROM wiki_pages WHERE id = ? AND project_id = ?", (wiki_page_id, project_id))
    return {"deleted": True, "wiki_page_id": wiki_page_id, "message": "Wiki page deleted from SQLite and ChromaDB."}

def validate_wiki_chunk_ids(project_id: str, wiki: SecurityWikiSchema) -> None:
    removed = 0
    sections = [
        *wiki.entry_points,
        *wiki.access_control_matrix,
        *wiki.vertical_helpers,
        *wiki.requirement_traces,
    ]
    for item in sections:
        if item.chunk_id and not code_chunk_exists(project_id, item.chunk_id):
            logger.warning("Wiki referenced unknown code chunk_id %s in project %s; clearing reference.", item.chunk_id, project_id)
            item.chunk_id = None
            removed += 1
    if removed:
        note = f" Removed {removed} invalid generated chunk reference(s) that were not found in source evidence."
        if note.strip() not in wiki.limitations:
            wiki.limitations = (wiki.limitations.rstrip() + note).strip()


def _enrich_wiki_references(project_id: str, wiki: SecurityWikiSchema) -> None:
    sections = [*wiki.entry_points, *wiki.access_control_matrix, *wiki.vertical_helpers, *wiki.requirement_traces]
    with db() as connection:
        for item in sections:
            if not item.chunk_id:
                item.source_reference_status = "missing"
                continue
            row = connection.execute(
                """SELECT c.start_line, c.end_line, f.file_path FROM code_chunks c
                   JOIN files f ON f.id = c.file_id WHERE c.project_id = ? AND c.id = ?""",
                (project_id, item.chunk_id),
            ).fetchone()
            if not row:
                item.source_reference_status = "invalid"
                continue
            item.source_reference_status = "validated"
            if hasattr(item, "file_path") and not getattr(item, "file_path", None):
                item.file_path = row["file_path"]
            if hasattr(item, "start_line") and not getattr(item, "start_line", None):
                item.start_line = row["start_line"]
            if hasattr(item, "end_line") and not getattr(item, "end_line", None):
                item.end_line = row["end_line"]


def wiki_storage_to_markdown(content: str) -> str:
    try:
        wiki = SecurityWikiSchema.model_validate_json(content)
    except ValidationError:
        return content
    return render_wiki_to_markdown(wiki)


def render_wiki_to_markdown(wiki: SecurityWikiSchema) -> str:
    module_name = _wiki_module_name(wiki)
    lines = [
        f"# Security Wiki: {module_name}",
        "",
        "## Overview",
        "",
        wiki.module_overview,
        "",
        "## Entry Points",
        "",
        "| Name | File | Lines | Description | Chunk ID |",
        "| --- | --- | --- | --- | --- |",
    ]
    if wiki.entry_points:
        for item in wiki.entry_points:
            lines.append(
                f"| {_md_cell(item.name)} | {_md_cell(item.file_path)} | {item.start_line}-{item.end_line} | "
                f"{_md_cell(item.description)} | {_md_cell(item.chunk_id or '')} |"
            )
    else:
        lines.append("| Not verified |  |  | No entry points were verified from the available evidence. |  |")
    lines.extend(
        [
            "",
            "## Access Control Matrix",
            "",
            "| Caller | Permission Check | File | Start Line | Chunk ID |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if wiki.access_control_matrix:
        for item in wiki.access_control_matrix:
            lines.append(
                f"| {_md_cell(item.caller)} | {_md_cell(item.permission_check)} | {_md_cell(item.file_path)} | "
                f"{item.start_line or ''} | {_md_cell(item.chunk_id or '')} |"
            )
    else:
        lines.append("| Not verified | Not verified |  |  |  |")
    lines.extend(["", "## Vertical Helpers", ""])
    if wiki.vertical_helpers:
        for item in wiki.vertical_helpers:
            chunk = f" (`{item.chunk_id}`)" if item.chunk_id else ""
            lines.append(f"- **{item.name}**{chunk}: {item.role} ({item.file_path})")
    else:
        lines.append("- No vertical helpers were verified from the available evidence.")
    lines.extend(["", "## Requirement Traces", ""])
    if wiki.requirement_traces:
        for item in wiki.requirement_traces:
            location = f" ({item.file_path})" if item.file_path else ""
            chunk = f" [`{item.chunk_id}`]" if item.chunk_id else ""
            lines.append(f"- **{item.requirement}**: {item.code_reference}{location}{chunk}")
    else:
        lines.append("- No requirement traces were verified from the available evidence.")
    lines.extend(["", "## Limitations", "", wiki.limitations])
    return "\n".join(lines)


def _wiki_module_name(wiki: SecurityWikiSchema) -> str:
    if wiki.entry_points:
        return wiki.entry_points[0].file_path
    if wiki.access_control_matrix:
        return wiki.access_control_matrix[0].file_path
    if wiki.vertical_helpers:
        return wiki.vertical_helpers[0].file_path
    return "Selected Module"


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


async def chat(project_id: str, request: ChatRequest) -> dict:
    execution_id = str(uuid.uuid4())
    started_at = now()
    started_perf = time.perf_counter()
    # ASK RETRIEVAL TRACE:
    # Retrieval function: retrieve_evidence_package
    # Parameters: query text=request.question, project_id=project_id, top_k=8, filters=None
    # Retrieval happens once for the request and returns both source chunks and wiki chunks.
    # Wiki context is fetched from the same shared package used by Compare.
    with db() as connection:
        evidence_package = retrieve_evidence_package(project_id, request.question, 10, connection, request.module_id)
    evidence = evidence_package["source_chunks"]
    wiki_context = evidence_package["wiki_chunks"]
    existence_searches = evidence_package.get("diagnostics", {}).get("repository_existence_searches", [])
    supplied_package = _freeze_shared_evidence_package(evidence, existence_searches)
    supplied_wiki_package = freeze_wiki_context(wiki_context)
    wiki_provenance = evidence_package["wiki_context"]
    provider, default_model = provider_for(request.provider)
    model = request.model or default_model
    session_id = str(uuid.uuid4())
    answer_id = str(uuid.uuid4())
    timestamp = now()
    with db() as connection:
        connection.execute(
            "INSERT INTO chat_sessions (id, project_id, module_id, model_provider, model_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, project_id, request.module_id, request.provider, model, timestamp),
        )
        connection.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, "user", request.question, None, timestamp),
        )
    if not evidence:
        content = "Not verified from the available source-code evidence."
        raw_model_response = None
        parsed_answer_json = None
        validation_status = "no_evidence"
        response_evidence = []
    else:
        messages = _chat_messages_for_provider(provider, request.question, evidence, wiki_context, existence_searches, model=model)
        prompt_composition = measure_prompt_components(messages, request.question, evidence, wiki_context, wiki_context_to_prompt)
        result = await safe_generate(provider, messages, model)
        raw_model_response = result.get("content") or ""
        if _is_timeout_result(result):
            content = "Model did not respond within the time limit."
            validation_status = "timeout"
            parsed_answer_json = None
            response_evidence = []
        elif result.get("ok") is False:
            content = result.get("error", {}).get("user_message") or "The model provider could not evaluate this request."
            parsed_answer_json = None
            validation_status = result.get("validation_status") or "provider_unavailable"
            response_evidence = []
        elif getattr(provider, "name", "").lower() == "ollama":
            content = result.get("content") or raw_model_response
            parsed_answer_json = result.get("parsed_answer_json")
            if not parsed_answer_json:
                content, parsed_answer_json = parse_simple_answer(raw_model_response)
            validation_status = result.get("validation_status", "valid_simple")
            response_evidence = evidence
        else:
            parsed, validation_status, invalid_refs = parse_chat_answer(raw_model_response, evidence, existence_searches)
            _record_chat_parse_outcome(result, validation_status, bool(raw_model_response))
            if parsed:
                limitations = list(parsed.limitations)
                if invalid_refs:
                    limitations.append(f"Model referenced invalid evidence IDs that were dropped: {', '.join(invalid_refs)}")
                parsed_payload = parsed.model_dump()
                parsed_payload["limitations"] = limitations
                parsed_payload["valid_evidence_refs"] = [ref for ref in parsed.evidence_refs if ref in {item["chunk_id"] for item in evidence}]
                parsed_payload["valid_existence_refs"] = [ref for ref in parsed.evidence_refs if re.fullmatch(r"X\d+", ref, re.IGNORECASE)]
                content = render_structured_answer_payload(parsed_payload)
                parsed_answer_json = json.dumps(parsed_payload)
                response_evidence = [] if parsed_payload["valid_existence_refs"] and not parsed_payload["valid_evidence_refs"] else _evidence_for_refs(evidence, parsed_payload["valid_evidence_refs"])
            else:
                content = raw_model_response or "Not verified from the available source-code evidence."
                parsed_answer_json = None
                response_evidence = evidence
                if validation_status == "text_fallback":
                    validation_status = "completed_with_warnings"
    with db() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages
            (id, session_id, role, content, evidence_json, raw_model_response, parsed_answer_json, validation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (answer_id, session_id, "assistant", content, json.dumps(response_evidence), raw_model_response, parsed_answer_json, validation_status, now()),
        )
    reference_validation = validate_model_references(content, evidence, existence_searches)
    config_snapshot = evaluation_configuration(project_id, [{"provider": request.provider, "model": model}])
    evaluation_id = str(uuid.uuid4())
    completed_at = now()
    with db() as connection:
        connection.execute("""INSERT INTO evaluations
            (id,project_id,module_path,question,chat_message_id,model_provider,model_name,answer_text,parsed_answer_json,evidence_json,wiki_context_json,validation_status,evaluation_config_hash,evaluation_type,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (evaluation_id,project_id,request.module_id,request.question,answer_id,request.provider,model,content,parsed_answer_json,json.dumps(response_evidence),json.dumps(wiki_context),validation_status,config_snapshot["evaluation_config_hash"],"model",completed_at))
    run_id = persist_formal_run({"run_id": execution_id, "project_id": project_id, "operation": "ask", "question": request.question, "provider_model": {"provider": request.provider, "model": model}, "answer": content, "primary_evidence": response_evidence, "supplied_source_evidence": evidence, "cited_source_evidence": response_evidence, "wiki_context": wiki_context, "execution_status": validation_status, "evaluation_config_hash": config_snapshot["evaluation_config_hash"], "evaluation_config": config_snapshot["evaluation_config"], "supplied_source_package_hash": supplied_package["shared_evidence_hash"], "supplied_wiki_package_hash": supplied_wiki_package["shared_wiki_context_hash"], "human_evaluation_id": evaluation_id, "started_at": started_at, "completed_at": completed_at})
    usage = persist_usage(execution_id=execution_id, run_id=run_id, project_id=project_id, operation="ask", provider=request.provider, model=model, normalized=normalize_usage(request.provider, result.get("usage") if evidence else {}), duration_ms=int((time.perf_counter() - started_perf) * 1000), composition=prompt_composition if evidence else {}, supplied_source=evidence, cited_source=response_evidence, wiki=wiki_context, source_hash=supplied_package["shared_evidence_hash"], wiki_hash=supplied_wiki_package["shared_wiki_context_hash"], status=validation_status, warnings=reference_validation["model_reference_warnings"])
    execution = _execution_details(
        execution_id, started_at, "ask", validation_status, request.question,
        evidence_package.get("diagnostics", {}).get("expanded_query", request.question), request.module_id, 10,
        evidence, wiki_context, request.provider, model, int((time.perf_counter() - started_perf) * 1000),
        (result.get("diagnostics", {}) if evidence else {}),
        (result.get("diagnostics", {}).get("raw_response") if evidence else None),
        {**evidence_package.get("diagnostics", {}), **supplied_package},
    )
    execution["provider"].update({"supplied_source_count": len(evidence), "cited_source_count": len(response_evidence), "supplied_source_chunk_ids": [item["chunk_id"] for item in evidence], "cited_source_chunk_ids": [item["chunk_id"] for item in response_evidence]})
    return {
        "session_id": session_id,
        "message_id": answer_id,
        "evaluation_id": evaluation_id,
        "answer": content,
        "evidence": response_evidence,
        "supplied_source_evidence": evidence,
        "cited_source_evidence": response_evidence,
        "supplied_source_chunk_ids": [item["chunk_id"] for item in evidence],
        "cited_source_chunk_ids": [item["chunk_id"] for item in response_evidence],
        "supplied_source_count": len(evidence),
        "cited_source_count": len(response_evidence),
        "usage": usage,
        "wiki_context": wiki_context,
        "wiki_context_provenance": wiki_provenance,
        "supplied_repository_existence_evidence": existence_searches,
        "supplied_evidence_categories": ["source_code", *(["repository_existence_metadata"] if existence_searches else []), *(["wiki_orientation"] if wiki_context else [])],
        **reference_validation,
        **config_snapshot,
        "run_id": run_id,
        "context_used": "raw code + wiki context" if wiki_context else "raw code evidence only",
        "validation_status": validation_status,
        "display_status": display_status_for(validation_status),
        "provider": request.provider,
        "model": model,
        "execution": execution,
    }


def parse_chat_answer(raw_content: str, evidence: list[dict], existence_searches: list[dict] | None = None) -> tuple[ChatAnswer | None, str, list[str]]:
    existence_searches = existence_searches or []
    valid_ids = {item["chunk_id"] for item in evidence}
    aliases = {f"E{index}": item["chunk_id"] for index, item in enumerate(evidence, 1)}
    existence_aliases = {f"X{index}" for index in range(1, len(existence_searches) + 1)}
    parsed = next(_validated_chat_answer_candidates(raw_content), None)
    if parsed is None:
        return None, "text_fallback" if raw_content.strip() else "empty_response", []
    parsed.evidence_refs = [aliases.get(ref.upper(), ref.upper() if ref.upper() in existence_aliases else ref) for ref in parsed.evidence_refs]
    invalid_refs = [ref for ref in parsed.evidence_refs if ref not in valid_ids and ref not in existence_aliases]
    if invalid_refs:
        parsed.evidence_refs = [ref for ref in parsed.evidence_refs if ref in valid_ids or ref in existence_aliases]
        return parsed, "valid_with_dropped_invalid_evidence_refs", invalid_refs
    return parsed, "valid_json", []


def _validated_chat_answer_candidates(raw_content: str):
    """Yield only complete JSON objects that satisfy the ChatAnswer contract."""
    decoder = json.JSONDecoder()
    seen = set()
    stripped = raw_content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.IGNORECASE | re.DOTALL)
    strict_text = fenced.group(1) if fenced else stripped
    candidates = []
    try:
        strict_payload = json.loads(strict_text)
        candidates.append(strict_payload)
    except (json.JSONDecodeError, TypeError):
        pass
    for index, char in enumerate(raw_content):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw_content, index)
        except json.JSONDecodeError:
            continue
        candidates.append(payload)
    for payload in candidates:
        if not isinstance(payload, dict):
            continue
        fingerprint = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        try:
            yield ChatAnswer.model_validate(payload)
        except ValidationError:
            continue


def _record_chat_parse_outcome(result: dict, validation_status: str, content_present: bool) -> None:
    """Make provider-success parser diagnostics terminal without changing provider text."""
    diagnostics = result.setdefault("diagnostics", {})
    processing = diagnostics.setdefault("processing", {})
    processing["response_received"] = True
    processing["content_present"] = content_present
    processing["parse_status"] = validation_status
    if validation_status in {"valid_json", "valid_with_dropped_invalid_evidence_refs"}:
        processing["schema_validation_status"] = "valid"
    elif validation_status == "text_fallback":
        processing["schema_validation_status"] = "failed_text_preserved"
    else:
        processing["schema_validation_status"] = "not_applicable"
    processing["evidence_validation_status"] = "backend_validation_completed" if content_present else "not_run_no_content"


def _evidence_for_refs(evidence: list[dict], refs: list[str]) -> list[dict]:
    if not refs:
        return evidence
    ref_set = set(refs)
    filtered = [item for item in evidence if item["chunk_id"] in ref_set]
    return filtered or evidence


def _uses_simple_prompt(provider) -> bool:
    return getattr(provider, "name", "").lower() == "ollama"


def _chat_messages_for_provider(provider, question: str, evidence: list[dict], wiki_context: list[dict], existence_searches: list[dict] | None = None, model: str | None = None) -> list[dict]:
    existence_blocks = repository_existence_to_prompt(existence_searches or [])
    if _uses_simple_prompt(provider):
        existence_section = f"\n\nREPOSITORY-WIDE EXISTENCE METADATA (system-generated search results; not source-code blocks):\n{existence_blocks}" if existence_blocks else ""
        return [
            {
                "role": "user",
                "content": SIMPLE_PROMPT_TEMPLATE.format(evidence_blocks=compact_evidence_to_prompt(evidence), existence_section=existence_section, wiki_context_blocks=wiki_context_to_prompt(wiki_context), query=question),
            }
        ]
    source_and_context = (
        f"Question: {question}\n\n"
        "A. Source-code evidence. Use this as proof for all claims:\n"
        f"{compact_evidence_to_prompt(evidence)}\n\n"
    )
    if existence_blocks:
        source_and_context += (
            "B. Repository-wide existence metadata. This is a system-generated search result, not a source-code block:\n"
            f"{existence_blocks}\n\n"
            "C. Generated wiki context. Use this only for orientation, not as proof:\n"
        )
    else:
        source_and_context += "B. Generated wiki context. Use this only for orientation, not as proof:\n"
    source_and_context += wiki_context_to_prompt(wiki_context)
    system_prompt = CHAT_JSON_PROMPT
    if presentation_configuration(getattr(provider, "name", None), model):
        system_prompt += "\n\n" + GPT51_CONCISE_PRESENTATION_PROMPT
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": source_and_context,
        },
    ]


def parse_simple_answer(raw_content: str) -> tuple[str, str]:
    lines = raw_content.splitlines()
    metadata: dict[str, str] = {}
    answer_lines: list[str] = []
    in_metadata = False
    for line in lines:
        upper = line.strip().upper()
        if any(upper.startswith(prefix) for prefix in ("FILE:", "LINE:", "CONFIDENCE:", "LIMITATIONS:")):
            in_metadata = True
            key, _, value = line.partition(":")
            metadata[key.strip().lower()] = value.strip()
            continue
        if in_metadata:
            answer_lines.append(line)
        else:
            answer_lines.append(line)
    answer = "\n".join(answer_lines).strip() or raw_content.strip() or "The evidence does not show this."
    parsed = {
        "answer": answer,
        "file": metadata.get("file", "unknown"),
        "line": metadata.get("line", "unknown"),
        "confidence": metadata.get("confidence", "low"),
        "limitations": metadata.get("limitations", ""),
        "validation_status": "valid_simple",
    }
    return answer, json.dumps(parsed)


async def compare_models(project_id: str, request: CompareRequest) -> dict:
    compare_execution_id = str(uuid.uuid4())
    compare_started_at = now()
    # COMPARE RETRIEVAL TRACE:
    # Retrieval function: retrieve_evidence_package
    # Parameters: query text=request.question, project_id=project_id, top_k=8, filters=None
    # Retrieval happens once and the same evidence package is passed to every selected provider.
    # Wiki context is fetched from the same shared package used by Ask.
    common_limit = get_settings().compare_source_chunk_limit
    with db() as connection:
        evidence_package = retrieve_evidence_package(project_id, request.question, common_limit, connection, request.module_id)
    evidence = list(evidence_package["source_chunks"])
    wiki_context = evidence_package["wiki_chunks"]
    existence_searches = evidence_package.get("diagnostics", {}).get("repository_existence_searches", [])
    shared_package = _freeze_shared_evidence_package(evidence, existence_searches)
    shared_wiki_package = freeze_wiki_context(wiki_context)
    selected_provider_names = list(dict.fromkeys(request.providers))
    provider_name_for = lambda selection: selection.split("::", 1)[0]
    unavailable_provider_names = []
    excluded_providers = []
    results = []
    selected_models = []
    for provider_selection in selected_provider_names:
        provider_name = provider_name_for(provider_selection)
        provider_execution_id = str(uuid.uuid4())
        provider_active = provider_name in active_provider_names()
        provider, default_model = provider_for(provider_name) if provider_active else (None, provider_name)
        selection_model = provider_selection.split("::", 1)[1] if "::" in provider_selection else None
        model_override = selection_model or ((request.provider_models or {}).get(provider_name) if request.provider_models else None)
        model = model_override or (default_model if provider_active else provider_name)
        selected_models.append({"provider": provider_name, "model": model})
        if not provider_active or not _selected_provider_available(provider_name, model):
            unavailable_provider_names.append(provider_selection)
            excluded_providers.append({**{"provider": provider_name, "reason": "Provider is unavailable or not configured."}, **({"selection_id": provider_selection} if "::" in provider_selection else {})})
        started = time.perf_counter()

        provider_evidence = evidence
        serialized_ids = [item["chunk_id"] for item in provider_evidence]
        expected_ids = shared_package["ordered_chunk_ids"]
        evidence_match = serialized_ids == expected_ids
        prompt_composition = {}
        if provider_selection in unavailable_provider_names:
            result = {"content": "", "ok": False, "validation_status": "provider_unavailable", "diagnostics": {}, "error": {"error_code": "MODEL_NOT_CONFIGURED", "user_message": f"{provider_name.title()} is unavailable because it is not configured.", "technical_message": None, "retryable": False, "provider": provider_name, "model": model, "execution_id": compare_execution_id}}
        elif provider_evidence and evidence_match and provider is not None:
            messages = _chat_messages_for_provider(provider, request.question, provider_evidence, wiki_context, existence_searches, model=model)
            prompt_composition = measure_prompt_components(messages, request.question, provider_evidence, wiki_context, wiki_context_to_prompt)
            result = await safe_generate(provider, messages, model)
        elif provider_evidence:
            result = {"content": "Comparison stopped because serialized evidence differs from the shared package.", "ok": False, "validation_status": "completed_with_evidence_mismatch", "diagnostics": {}}
        else:
            result = {}
        if provider_evidence:
            raw_answer = result.get("content") or "Not verified from the available source-code evidence."
            answer_preview = None
            if _is_timeout_result(result):
                answer = "Model did not respond within the time limit."
                parsed_answer_json = json.dumps(
                    {
                        "answer": answer,
                        "confidence": "none",
                        "validation_status": "timeout",
                        "display_status": "Model timed out",
                        "evidence_refs": [],
                        "limitations": "Local model timed out. Use a cloud model for comparison.",
                    }
                )
                validation_status = "timeout"
            elif result.get("ok") is False:
                provider_error = result.get("error", {})
                safe_reason = provider_error.get("user_message") or f"{provider_name.title()} could not complete the request."
                answer = safe_reason
                parsed_answer_json = json.dumps({"answer": answer, "confidence": "none", "limitations": [safe_reason], "evidence_refs": []})
                validation_status = result.get("validation_status") or "provider_unavailable"
            elif getattr(provider, "name", "").lower() == "ollama":
                answer = result.get("content") or raw_answer
                parsed_answer_json = result.get("parsed_answer_json")
                if not parsed_answer_json:
                    answer, parsed_answer_json = parse_simple_answer(raw_answer)
                validation_status = result.get("validation_status", "valid_simple")
            else:
                parsed, validation_status, invalid_refs = parse_chat_answer(raw_answer, provider_evidence, existence_searches)
                _record_chat_parse_outcome(result, validation_status, bool(raw_answer))
                if parsed:
                    limitations = list(parsed.limitations)
                    if invalid_refs:
                        limitations.append(f"Model referenced invalid evidence IDs that were dropped: {', '.join(invalid_refs)}")
                    parsed_payload = parsed.model_dump()
                    parsed_payload["limitations"] = limitations
                    parsed_payload["valid_evidence_refs"] = [ref for ref in parsed.evidence_refs if ref in {item["chunk_id"] for item in provider_evidence}]
                    parsed_payload["valid_existence_refs"] = [ref for ref in parsed.evidence_refs if re.fullmatch(r"X\d+", ref, re.IGNORECASE)]
                    answer_preview = parsed.answer
                    answer = render_structured_answer_payload(parsed_payload)
                    parsed_answer_json = json.dumps(parsed_payload)
                else:
                    answer = raw_answer
                    parsed_answer_json = None
                    if validation_status == "text_fallback":
                        validation_status = "completed_with_warnings"
        else:
            answer = "Not verified from the available source-code evidence."
            parsed_answer_json = None
            validation_status = "no_evidence"
        cited_evidence = provider_evidence
        if parsed_answer_json:
            try:
                parsed_for_refs = json.loads(parsed_answer_json)
                refs = parsed_for_refs.get("valid_evidence_refs") or parsed_for_refs.get("evidence_refs") or []
                existence_refs = parsed_for_refs.get("valid_existence_refs") or []
                cited_evidence = [] if existence_refs and not refs else (_evidence_for_refs(provider_evidence, refs) if refs else provider_evidence)
            except (TypeError, json.JSONDecodeError):
                cited_evidence = provider_evidence
        prompt_fit = result.get("diagnostics", {}).get("prompt_fit", {"applicable": False, "passed": True, "provider": provider_name, "model": model, "count_type": "provider_capacity_not_configured"})
        effective_context_valid = bool(prompt_fit.get("passed"))
        reference_validation = validate_model_references(answer, provider_evidence, existence_searches)
        route_warnings = (_unsupported_route_claims(answer, provider_evidence) if provider_evidence else []) + reference_validation["model_reference_warnings"]
        if route_warnings and validation_status not in {"completed_with_evidence_mismatch", "timeout", "no_evidence", "provider_context_incompatible", "context_limit_exceeded"}:
            validation_status = "completed_with_warnings"
        if provider_evidence and not evidence_match:
            validation_status = "completed_with_evidence_mismatch"
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = persist_usage(execution_id=provider_execution_id, run_id=compare_execution_id, project_id=project_id, operation="compare", provider=provider_name, model=model, normalized=normalize_usage(provider_name, result.get("usage", {})), duration_ms=latency_ms, composition=prompt_composition, supplied_source=provider_evidence, cited_source=cited_evidence, wiki=wiki_context, source_hash=shared_package["shared_evidence_hash"], wiki_hash=shared_wiki_package["shared_wiki_context_hash"], status=validation_status, warnings=route_warnings)
        evaluation_id = str(uuid.uuid4())
        with db() as connection:
            connection.execute(
                """
                INSERT INTO evaluations
                (id, project_id, module_path, question, chat_message_id, model_provider, model_name, answer_text,
                 parsed_answer_json, evidence_json, wiki_context_json, validation_status, latency_ms, estimated_cost, evaluation_config_hash, evaluation_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    project_id,
                    request.module_id,
                    request.question,
                    None,
                    provider_name,
                    model,
                    answer,
                    parsed_answer_json,
                    json.dumps(provider_evidence),
                    json.dumps(wiki_context),
                    validation_status,
                    latency_ms,
                    0.0 if provider_name == "ollama" else None,
                    evaluation_configuration(project_id, selected_models)["evaluation_config_hash"],
                    "model",
                    now(),
                ),
            )
        results.append(
            {
                "evaluation_id": evaluation_id,
                "provider": provider_name,
                "selection_id": provider_selection,
                "model": model,
                "answer": answer,
                "full_answer": answer,
                "answer_preview": answer_preview or answer,
                "latency_ms": latency_ms,
                "validation_status": validation_status,
                "display_status": display_status_for(validation_status),
                "execution": _execution_details(
                    provider_execution_id, compare_started_at, "compare", validation_status, request.question,
                    evidence_package.get("diagnostics", {}).get("expanded_query", request.question), request.module_id, 10,
                    provider_evidence, wiki_context, provider_name, model, latency_ms,
                    result.get("diagnostics", {}) if provider_evidence else {},
                    result.get("diagnostics", {}).get("raw_response") if provider_evidence else None,
                    {**evidence_package.get("diagnostics", {}), **shared_package, "retrieved_chunk_ids": expected_ids, "serialized_chunk_ids": serialized_ids, "evidence_package_match": evidence_match},
                ),
                "shared_evidence_package_id": shared_package["shared_evidence_package_id"],
                "shared_evidence_hash": shared_package["shared_evidence_hash"],
                "serialized_chunk_ids": serialized_ids,
                "supplied_source_chunk_ids": serialized_ids,
                "cited_source_chunk_ids": [item["chunk_id"] for item in cited_evidence],
                "supplied_source_count": len(provider_evidence),
                "cited_source_count": len(cited_evidence),
                "supplied_source_package_hash": shared_package["shared_evidence_hash"],
                "supplied_wiki_chunk_ids": shared_wiki_package["ordered_wiki_chunk_ids"],
                "supplied_wiki_package_hash": shared_wiki_package["shared_wiki_context_hash"],
                "serialized_source_character_count": len(compact_evidence_to_prompt(provider_evidence)),
                "serialized_wiki_character_count": len(wiki_context_to_prompt(wiki_context)),
                "serialized_existence_character_count": len(repository_existence_to_prompt(existence_searches)),
                "supplied_repository_existence_evidence": existence_searches,
                "supplied_evidence_categories": ["source_code", *(["repository_existence_metadata"] if existence_searches else []), *(["wiki_orientation"] if wiki_context else [])],
                "usage": usage,
                "evidence_package_match": evidence_match,
                "effective_context_valid": effective_context_valid,
                "prompt_fit_validation": prompt_fit,
                "warnings": route_warnings,
                **reference_validation,
                **shared_wiki_package,
                "evaluation_status": "not_scored",
                "error": result.get("error"),
            }
        )
    completed = [item for item in results if item["validation_status"] not in {"timeout", "no_evidence", "error", "provider_unavailable", "provider_context_incompatible", "context_limit_exceeded"}]
    source_match = bool(results) and all(item.get("supplied_source_chunk_ids") == shared_package["ordered_chunk_ids"] and item.get("supplied_source_package_hash") == shared_package["shared_evidence_hash"] for item in results)
    wiki_match = bool(results) and all(item.get("supplied_wiki_chunk_ids") == shared_wiki_package["ordered_wiki_chunk_ids"] and item.get("supplied_wiki_package_hash") == shared_wiki_package["shared_wiki_context_hash"] for item in results)
    config_snapshot = evaluation_configuration(project_id, selected_models)
    comparison_model_count = len(results)
    compatible_config = bool(config_snapshot.get("evaluation_config_hash"))
    effective_context_valid = bool(results) and all(item.get("effective_context_valid", False) for item in results)
    rq2_eligible = len(completed) >= 2 and source_match and wiki_match and compatible_config and effective_context_valid
    invalid_reason = None if rq2_eligible else ("Comparison is not methodologically valid because at least one provider could not evaluate the complete prompt without truncation." if not effective_context_valid else ("Single-model diagnostic run — not eligible for RQ2 comparison." if len(completed) == 1 else "Comparison is not methodologically valid because at least two models did not complete with identical source and Wiki packages."))
    persist_formal_run({"run_id": compare_execution_id, "project_id": project_id, "operation": "compare", "question": request.question, "provider_model": selected_models, "answer": results, "primary_evidence": evidence, "supplied_source_evidence": evidence, "cited_source_evidence": [], "wiki_context": wiki_context, "execution_status": "completed" if results else "failed", "comparison_metadata": {**shared_package, **shared_wiki_package, "comparison_model_count": comparison_model_count, "completed_model_count": len(completed), "primary_evidence_match": source_match, "wiki_context_match": wiki_match, "effective_context_valid": effective_context_valid, "comparison_valid": rq2_eligible, "rq2_comparison_eligible": rq2_eligible}, "evaluation_config_hash": config_snapshot["evaluation_config_hash"], "evaluation_config": config_snapshot["evaluation_config"], "supplied_source_package_hash": shared_package["shared_evidence_hash"], "supplied_wiki_package_hash": shared_wiki_package["shared_wiki_context_hash"], "started_at": compare_started_at, "completed_at": now()})
    return {
        "question": request.question, "evidence": evidence, "wiki_context": wiki_context, "results": results,
        "repository_existence_evidence": existence_searches,
        "supplied_evidence_categories": ["source_code", *(["repository_existence_metadata"] if existence_searches else []), *(["wiki_orientation"] if wiki_context else [])],
        "wiki_context_provenance": evidence_package.get("wiki_context", {"requested": True, "available_wiki_count": 0, "candidate_wiki_chunk_count": len(wiki_context), "selected_wiki_chunk_count": len(wiki_context), "selected_wiki_chunks": wiki_context}),
        "comparison_valid": rq2_eligible,
        "primary_evidence_match": source_match,
        "wiki_context_match": wiki_match,
        "effective_context_valid": effective_context_valid,
        "comparison_invalid_reason": invalid_reason,
        "comparison_model_count": comparison_model_count, "completed_model_count": len(completed), "rq2_comparison_eligible": rq2_eligible,
        **shared_wiki_package, **config_snapshot,
        "shared_evidence_package_id": shared_package["shared_evidence_package_id"],
        "shared_evidence_hash": shared_package["shared_evidence_hash"],
        "excluded_providers": excluded_providers,
        "run_summary": {
            "execution_id": compare_execution_id, "question": request.question,
            "shared_evidence_package_id": shared_package["shared_evidence_package_id"],
            "shared_evidence_hash": shared_package["shared_evidence_hash"],
            "comparison_valid": rq2_eligible,
            "effective_context_valid": effective_context_valid,
            "comparison_invalid_reason": invalid_reason,
            "comparison_model_count": comparison_model_count, "completed_model_count": len(completed), "rq2_comparison_eligible": rq2_eligible,
            **shared_wiki_package, **config_snapshot,
            "started_at": compare_started_at, "completed_at": now(),
            "total_duration": sum(item["latency_ms"] for item in results),
            "selected_models": selected_models,
        },
    }



def _unsupported_route_claims(answer: str, source_chunks: list[dict]) -> list[dict]:
    supported = {str(item.get("effective_route")) for item in source_chunks if item.get("route_resolution_status") == "resolved" and item.get("effective_route") is not None}
    claims = {claim.rstrip(".,;:)") for claim in re.findall(r"(?<![\w:])/(?:[A-Za-z0-9_.{}-]+(?:/[A-Za-z0-9_.{}-]+)*)", answer or "")}
    unsupported = sorted(claim for claim in claims if claim not in supported)
    return [{"code": "unsupported_route_claim", "claim": claim, "supported_effective_routes": sorted(supported), "message": "The answer contains a route not supported by resolved route metadata; the original answer is preserved."} for claim in unsupported]


def _freeze_shared_evidence_package(source_chunks: list[dict], existence_searches: list[dict] | None = None) -> dict:
    canonical = [{"chunk_id": item["chunk_id"], "file_path": item.get("file_path"), "start_line": item.get("start_line"), "end_line": item.get("end_line"), "code_snippet": item.get("code_snippet", "")} for item in source_chunks]
    existence_searches = existence_searches or []
    canonical_existence = []
    for search in existence_searches:
        strong_semantic_hits = [hit for hit in (search.get("semantic_hits") or []) if float(hit.get("similarity") or 0) >= 0.65]
        canonical_existence.append({
            "concept_searched": search.get("concept_searched"),
            "search_terms": search.get("search_terms") or [],
            "search_scope": search.get("search_scope"),
            "scanned_chunk_count": int(search.get("scanned_chunk_count") or 0),
            "candidate_count": int(search.get("candidate_count") or 0),
            "exact_symbol_hit_count": len(search.get("exact_symbol_hits") or []),
            "lexical_hit_count": len(search.get("lexical_hits") or []),
            "strong_semantic_hit_count": len(strong_semantic_hits),
            "matching_chunk_ids": sorted({hit.get("chunk_id") for hit in [*(search.get("exact_symbol_hits") or []), *(search.get("lexical_hits") or []), *strong_semantic_hits] if hit.get("chunk_id")}),
            "existence_result": search.get("existence_result"),
        })
    payload = canonical if not canonical_existence else {"source_chunks": canonical, "repository_existence_checks": canonical_existence}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=bool(canonical_existence), separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"shared_evidence_package_id": digest[:12], "shared_evidence_hash": digest, "ordered_chunk_ids": [item["chunk_id"] for item in source_chunks], "repository_existence_checks": canonical_existence, "repository_existence_check_count": len(canonical_existence)}


def _serialized_evidence_metadata(source_chunks: list[dict], roles: dict | None = None) -> list[dict]:
    roles = roles or {}
    return [{"chunk_index": index, "chunk_id": item["chunk_id"], "file_path": item.get("file_path"), "symbol": item.get("symbol_name"), "start_line": item.get("start_line"), "end_line": item.get("end_line"), "source_type": item.get("source_type", "code"), "evidence_role": roles.get(item["chunk_id"], []), "serialized_character_count": len(compact_evidence_to_prompt([item]))} for index, item in enumerate(source_chunks, 1)]


def _execution_details(
    execution_id: str, started_at: str, operation: str, status: str, query: str,
    expanded_query: str, selected_file: str | None, requested_top_k: int,
    source_chunks: list[dict], wiki_chunks: list[dict], provider: str, model: str,
    duration_ms: int, processing: dict, sanitized_raw_response: str | None = None,
    retrieval: dict | None = None,
) -> dict:
    retrieval = retrieval or {}
    diagnostic_envelope = processing.get("envelope", {}) if isinstance(processing, dict) else {}
    processing_stage = processing.get("processing", processing) if isinstance(processing, dict) else {}
    timeout = getattr(get_settings(), f"{provider}_timeout_seconds", None)
    normalized_status = {
        "valid_json": "completed",
        "valid_json_repaired": "completed_with_warnings",
        "valid_simple": "completed_with_warnings",
        "timeout": "provider_timeout",
        "invalid_json_fallback": "response_parse_failed",
        "text_fallback": "completed_with_warnings",
        "no_evidence": "evidence_validation_failed",
        "no_source_evidence": "evidence_validation_failed",
        "error": "provider_unavailable",
    }.get(status, status)
    if normalized_status in {"completed", "completed_with_warnings"} or status.startswith("valid"):
        processing_stage = {**processing_stage, "response_received": True, "content_present": processing_stage.get("content_present", True), "parse_status": processing_stage.get("parse_status", status), "schema_validation_status": "valid" if status == "valid_json" else processing_stage.get("schema_validation_status", "not_required"), "evidence_validation_status": "valid"}
    return {
        "execution_id": execution_id,
        "started_at": started_at,
        "completed_at": now(),
        "operation": operation,
        "status": normalized_status,
        "query": query,
        "expanded_query": expanded_query,
        "selected_file": selected_file,
        "enumeration_intent": retrieval.get("enumeration_intent", False),
        "requested_top_k": requested_top_k,
        "retrieval": {
            "vector_candidate_count": retrieval.get("vector_candidate_count", 0),
            "lexical_candidate_count": retrieval.get("lexical_candidate_count", 0),
            "selected_file_candidate_count": retrieval.get("selected_file_candidate_count", 0),
            "deduplicated_candidate_count": retrieval.get("deduplicated_candidate_count", len(source_chunks)),
            "final_source_evidence_count": len(source_chunks),
            "wiki_orientation_count": len(wiki_chunks),
            "selected_file_chunks_in_final": retrieval.get("selected_file_chunks_in_final", 0),
            "distinct_selected_file_symbols": retrieval.get("distinct_selected_file_symbols", 0),
            "candidates_removed_by_deduplication": retrieval.get("candidates_removed_by_deduplication", 0),
            "requested_evidence_roles": retrieval.get("requested_evidence_roles", []),
            "satisfied_evidence_roles": retrieval.get("satisfied_evidence_roles", []),
            "unsatisfied_evidence_roles": retrieval.get("unsatisfied_evidence_roles", []),
            "evidence_role_by_chunk_id": retrieval.get("evidence_role_by_chunk_id", retrieval.get("evidence_role_by_chunk", {})),
            "role_satisfaction_reason": retrieval.get("role_satisfaction_reason", {}),
            "repository_concept_existence_intent": retrieval.get("repository_concept_existence_intent", False),
            "repository_existence_concepts_requested": retrieval.get("repository_existence_concepts_requested", []),
            "repository_existence_searches": retrieval.get("repository_existence_searches", []),
            "shared_evidence_package_id": retrieval.get("shared_evidence_package_id"),
            "shared_evidence_hash": retrieval.get("shared_evidence_hash"),
            "retrieved_chunk_ids": retrieval.get("retrieved_chunk_ids", [item["chunk_id"] for item in source_chunks]),
            "serialized_chunk_ids": retrieval.get("serialized_chunk_ids", [item["chunk_id"] for item in source_chunks]),
            "evidence_package_match": retrieval.get("evidence_package_match", True),
        },
        "provider": {
            "provider": provider, "model": model, "timeout_seconds": timeout,
            "source_chunks_sent": len(source_chunks), "source_chunk_count": len(source_chunks), "wiki_chunks_sent": len(wiki_chunks),
            "serialized_source_characters": len(compact_evidence_to_prompt(source_chunks)),
            "effective_model_configuration": _effective_model_configuration(provider, model, timeout),
            "prompt_fit_validation": processing.get("prompt_fit") if isinstance(processing, dict) else None,
            "evidence_supplied_to_model": _serialized_evidence_metadata(source_chunks, retrieval.get("evidence_role_by_chunk_id", retrieval.get("evidence_role_by_chunk", {}))),
            "request_duration_ms": duration_ms, "http_status": None, "retry_count": 0,
            **diagnostic_envelope,
            **({"sanitized_raw_response": sanitized_raw_response} if sanitized_raw_response is not None else {}),
        },
        "processing": {
            "response_received": processing_stage.get("response_received", False),
            "content_present": processing_stage.get("content_present", False),
            "content_length": processing_stage.get("content_length", processing_stage.get("raw_response_length", 0)),
            "raw_response_length": processing_stage.get("raw_response_length"),
            "think_tags_removed": processing_stage.get("think_tags_removed", False),
            "parser": processing_stage.get("parser", "unknown"),
            "parse_status": processing_stage.get("parse_status", status),
            "schema_validation_status": processing_stage.get("schema_validation_status", "not_required"),
            "evidence_validation_status": processing_stage.get("evidence_validation_status", "backend_evidence_attached"),
        },
        "error": None,
    }


def _effective_model_configuration(provider: str, model: str, timeout) -> dict:
    settings = get_settings()
    common = {"exact_model_tag": model, "timeout_seconds": timeout, "prompt_serialization_version": settings.prompt_serialization_version, **presentation_configuration(provider, model)}
    if provider == "ollama":
        return {**common, "reasoning": "enabled" if settings.ollama_think_enabled else "disabled", "temperature": 0.1, "top_p": None, "num_predict": settings.ollama_num_predict, "context_length": settings.ollama_context_length}
    if provider == "groq":
        return {**common, "reasoning_effort": settings.groq_reasoning_effort or "provider_default", "reasoning_format": settings.groq_reasoning_format, "include_reasoning": settings.groq_include_reasoning, "temperature": "provider_default", "max_output_tokens": settings.groq_max_output_tokens, "base_url": settings.groq_base_url}
    if provider == "openrouter":
        return {**common, "temperature": 0.1, "max_output_tokens": settings.openrouter_max_output_tokens, "base_url": settings.openrouter_base_url, "deployment_route": f"{settings.openrouter_model} accessed through OpenRouter"}
    return {**common, "temperature": 0.1}


async def safe_generate(provider, messages: list[dict], model: str) -> dict:
    preflight = prompt_fit_preflight(provider, messages, model)
    if not preflight["passed"]:
        return context_incompatible_response(preflight)
    try:
        result = await provider.generate(messages, model)
        diagnostics = dict(result.get("diagnostics") or {})
        diagnostics["prompt_fit"] = preflight
        result["diagnostics"] = diagnostics
        return result
    except TimeoutError:
        return timeout_response()
    except Exception as exc:
        return {
            "content": "The model provider failed before returning a response.\n\nNot verified from the available source-code evidence.",
            "raw": {"error": "MODEL_EXECUTION_FAILED"},
            "error": {"error_code": "MODEL_EXECUTION_FAILED", "user_message": "The model could not complete this request.", "technical_message": type(exc).__name__, "retryable": True, "provider": getattr(provider, "name", None), "model": model, "execution_id": None},
            "ok": False,
        }


def timeout_response() -> dict:
    return {
        "content": "Model did not respond within the time limit.",
        "answer": "Model did not respond within the time limit.",
        "confidence": "none",
        "validation_status": "timeout",
        "display_status": "Model timed out",
        "evidence_refs": [],
        "limitations": "Local model timed out. Use a cloud model for comparison.",
        "raw": {"error": "timeout"},
        "error": {"error_code": "OLLAMA_TIMEOUT", "user_message": f"Qwen did not finish within {int(get_settings().ollama_timeout_seconds)} seconds.", "technical_message": None, "retryable": True, "provider": "ollama", "model": None, "execution_id": None},
        "ok": False,
    }


def _is_timeout_result(result: dict) -> bool:
    return result.get("validation_status") == "timeout" or result.get("raw", {}).get("error") == "timeout"


def verify(request: VerificationRequest) -> dict:
    verification_id = str(uuid.uuid4())
    with db() as connection:
        connection.execute(
            "INSERT INTO verifications (id, target_type, target_id, verdict, human_comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (verification_id, request.target_type, request.target_id, request.verdict, request.human_comment, now()),
        )
    return {"id": verification_id, **request.model_dump(), "created_at": now()}


def get_evaluation(project_id: str, evaluation_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM evaluations WHERE id=? AND project_id=?", (evaluation_id, project_id)).fetchone()
    if not row:
        raise ValueError("Evaluation not found")
    return dict(row)


def score_evaluation(evaluation_id: str, request: EvaluationScoreRequest, project_id: str | None = None) -> dict:
    payload = request.model_dump()
    if payload.get("evidence_discipline") is None and request.evidence_quality is not None:
        payload["evidence_discipline"] = request.evidence_quality
    notes = payload["notes"] if payload["notes"] is not None else payload["evaluator_comment"]
    hallucination = payload["hallucination"] if payload["hallucination"] is not None else payload["hallucination_flag"]
    with db() as connection:
        existing = connection.execute("SELECT validation_status FROM evaluations WHERE id = ? AND (? IS NULL OR project_id = ?)", (evaluation_id, project_id, project_id)).fetchone()
        if not existing:
            raise ValueError("Evaluation not found")
        if existing and existing["validation_status"] in {"completed_with_evidence_mismatch", "context_limit_exceeded", "provider_context_incompatible"}:
            return {"id": evaluation_id, "error": "Comparison is not methodologically valid because providers received different evidence.", "scoring_allowed": False}
        connection.execute(
            """
            UPDATE evaluations
            SET correctness_score = ?,
                evidence_discipline_score = ?,
                source_reference_accuracy_score = ?,
                verdict = ?,
                correct_file_path = ?,
                correct_code_block = ?,
                explanation_quality = ?,
                completeness = ?,
                hallucination_flag = ?,
                usefulness = ?,
                evaluator_comment = ?,
                human_comment = ?
                , evaluated_at = ?
            WHERE id = ?
            """,
            (
                payload["correctness"],
                payload["evidence_discipline"],
                payload["source_reference_accuracy"],
                payload["verdict"],
                None,
                None,
                payload["explanation_quality"],
                payload["completeness"],
                None if hallucination is None else int(hallucination),
                payload["usefulness"],
                notes,
                notes,
                now(),
                evaluation_id,
            ),
        )
        if request.evidence_quality is not None:
            connection.execute("UPDATE evaluations SET evidence_quality_score = ? WHERE id = ?", (request.evidence_quality, evaluation_id))
        row = connection.execute("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
    return dict(row) if row else {"id": evaluation_id, "error": "Evaluation not found"}


def export_project(project_id: str, export_format: str, auditor_name: str = "Unknown") -> tuple[str, str, str]:
    from app.services.report_service import build_project_report, render_html, render_markdown, render_pdf
    report = build_project_report(project_id, auditor_name)
    project = report["project"]
    project_name = _safe_filename(project.get("name") or project_id)
    if export_format == "pdf":
        return "application/pdf", f"{project_name}_evaluation_report.pdf", render_pdf(report)
    if export_format == "html":
        return "text/html; charset=utf-8", f"{project_name}_evaluation_report.html", render_html(report)
    if export_format in {"md", "markdown"}:
        return "text/markdown; charset=utf-8", f"{project_name}_evaluation_report.md", render_markdown(report)
    raise ValueError("Export format must be pdf, html, or markdown")


def _export_data(project_id: str) -> dict:
    with db() as connection:
        project_row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project_row:
            raise ValueError("Project not found")
        project = dict(project_row)
        wiki_pages = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM wiki_pages WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
            ).fetchall()
        ]
        chat_messages = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.*, s.model_provider, s.model_name, s.project_id
                FROM chat_messages m
                JOIN chat_sessions s ON s.id = m.session_id
                WHERE s.project_id = ?
                ORDER BY m.created_at
                """,
                (project_id,),
            ).fetchall()
        ]
        evaluations = [dict(row) for row in connection.execute("SELECT * FROM evaluations WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()]
        formal_runs = [dict(row) for row in connection.execute("SELECT * FROM formal_runs WHERE project_id = ? AND run_purpose = 'formal_evaluation' ORDER BY timestamp DESC", (project_id,)).fetchall()]
        model_usage = [dict(row) for row in connection.execute("SELECT * FROM model_usage WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()]
        verifications = [dict(row) for row in connection.execute("SELECT * FROM verifications ORDER BY created_at DESC").fetchall()]
    return {
        "project": project,
        "wiki_pages": _deduplicate_wiki_pages(wiki_pages),
        "chat_messages": chat_messages,
        "evaluations": evaluations,
        "formal_runs": formal_runs,
        "model_usage": model_usage,
        "verifications": verifications,
        "limitations": ["LLM answers are valid only when backed by retrieved source evidence."],
    }


def _markdown_export(data: dict) -> str:
    project = data["project"]
    generated_at = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# Security Audit Report — {project['name']}",
        "",
        f"**Generated:** {generated_at}",
        "",
        f"**Auditor:** {data.get('auditor_name') or 'Unknown'}",
        "",
        f"**Project session ID:** {project['id']}",
        "",
        f"**Chat session IDs:** {', '.join(_chat_session_ids(data['chat_messages'])) or 'None recorded'}",
        "",
        "**Tool:** SecurityCodeWiki MVP v1.0",
        "",
        "**Thesis:** Evidence-Backed Access Control Comprehension Using LLM-Assisted RAG",
        "",
        "---",
        "## 1. Project Information",
        "",
        f"- Name: {project['name']}",
        f"- Project ID: {project['id']}",
        f"- Source type: {project['source_type']}",
        f"- Import date: {project.get('created_at') or ''}",
        f"- Indexed files count: {project.get('files_indexed') or 0}",
        f"- Chunk count: {project.get('chunks_indexed') or 0}",
        f"- Repository URL: {project.get('repo_url') or ''}",
        f"- Commit hash: {project.get('commit_hash') or ''}",
        f"- Security goal: {project.get('security_goal') or ''}",
        "",
        "## Security Wiki Context",
        "",
        EVIDENCE_DISCLAIMER,
    ]
    for page in data["wiki_pages"]:
        lines.extend(["", f"### {page['title']}", "", wiki_storage_to_markdown(page.get("content_markdown") or "")])
    if not data["wiki_pages"]:
        lines.extend(["", "No Security Wiki pages have been generated yet."])

    lines.extend(["", "## Audit Questions & Answers"])
    for index, item in enumerate(_qa_pairs_with_attempts(data["chat_messages"]), start=1):
        answer = item["answer"]
        verification = _verification_for(answer.get("id"), data["verifications"])
        evaluation = _evaluation_for_chat(answer.get("id"), data["evaluations"])
        evidence = _json_list(answer.get("evidence_json"))
        lines.extend(
            [
                "",
                f"### Q{index}: {item['question'].get('content') or 'Untitled question'}{_attempt_suffix(item)}",
                "",
                f"**Asked:** {item['question'].get('created_at') or ''}",
                "",
                f"**Answered:** {answer.get('created_at') or ''}",
                "",
                f"**Model:** {answer.get('model_provider') or ''} / {answer.get('model_name') or ''}",
                "",
                f"**Validation:** {answer.get('validation_status') or ''}",
                "",
                f"**Answer:** {answer.get('content') or ''}",
                "",
                "#### Evidence",
                "",
                "| # | File | Symbol | Lines | Snippet |",
                "|---|------|--------|-------|---------|",
            ]
        )
        if evidence:
            for evidence_index, evidence_item in enumerate(evidence, start=1):
                lines.append(
                    f"| {evidence_index} | {_md_cell(evidence_item.get('file_path') or '')} | "
                    f"{_md_cell(evidence_item.get('symbol_name') or '')} | "
                    f"{evidence_item.get('start_line') or ''}-{evidence_item.get('end_line') or ''} | "
                    f"{_md_cell(_shorten(evidence_item.get('code_snippet') or ''))} |"
                )
        else:
            lines.append("|  |  |  |  | No evidence cards were stored. |")
        lines.extend(
            [
                "",
                "#### Auditor Verification",
                "",
                f"- Verdict: {verification.get('verdict') if verification else 'Not verified'}",
                f"- Comment: {(verification or {}).get('human_comment') or ''}",
                "",
                "#### Evaluation Scores",
                "",
                f"- {_evaluation_human_score(evaluation) if evaluation else 'Not yet scored'}",
                f"- Hallucination: {_hallucination_label(evaluation)}",
            ]
        )

    lines.extend(
        [
            "",
            "## 4. Model Comparison Summary",
            "",
            "| Question | Model | Evidence Count | Human Score |",
            "|----------|-------|----------------|-------------|",
        ]
    )
    if data["evaluations"]:
        for item in data["evaluations"]:
            evidence_count = len(_json_list(item.get("evidence_json")))
            lines.append(
                f"| {_md_cell(item.get('question') or '')} | {_md_cell((item.get('model_provider') or '') + ' / ' + (item.get('model_name') or ''))} | "
                f"{evidence_count} | {_md_cell(_evaluation_human_score(item))} |"
            )
    else:
        lines.append("|  |  | 0 | Not yet scored |")
        lines.extend(["", "*No evaluations are recorded. Scores must be entered manually in the audit workspace before they appear here.*"])

    lines.extend(["", "## 5. Evaluation Notes", ""])
    notes = [item.get("evaluator_comment") or item.get("human_comment") for item in data["evaluations"] if item.get("evaluator_comment") or item.get("human_comment")]
    for item in data["verifications"]:
        note = f"{item['target_type']} {item['target_id']}: {item['verdict']} {item.get('human_comment') or ''}".strip()
        notes.append(note)
    if notes:
        lines.extend(f"- {note}" for note in notes)
    else:
        lines.append("No human evaluation notes have been recorded yet.")
    lines.extend(
        [
            "",
            "## 6. System Limitations",
            "",
            "- Parser is heuristic/AST-based; line ranges may be imprecise for complex syntax",
            "- Retrieval is RAG-based; questions outside indexed content return no evidence",
            "- Access control conclusions are LLM-assisted; human verification is required",
        ]
    )
    return "\n".join(lines)


def _html_export(data: dict) -> str:
    project = data["project"]
    generated_at = datetime.now(timezone.utc).isoformat()
    qa_pairs = _qa_pairs_with_attempts(data["chat_messages"])
    model_names = sorted({f"{item.get('model_provider') or ''}/{item.get('model_name') or ''}" for item in data["evaluations"] if item.get("model_provider")})
    if not model_names:
        model_names = sorted({f"{item.get('model_provider') or ''}/{item.get('model_name') or ''}" for item in data["chat_messages"] if item.get("model_provider")})
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Security Audit Report</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;max-width:1100px;margin:0 auto;padding:20px;color:#212121}",
        "h1{color:#1a237e;border-bottom:3px solid #1a237e;padding-bottom:8px}",
        "h2{color:#283593;border-bottom:1px solid #c5cae9;margin-top:40px}",
        "h3{color:#3949ab;margin-top:24px}",
        "table{border-collapse:collapse;width:100%;margin:12px 0}",
        "th{background:#3949ab;color:#fff;padding:8px 12px;text-align:left}",
        "td{border:1px solid #c5cae9;padding:8px 12px;vertical-align:top}",
        "tr:nth-child(even){background:#f5f5f5}",
        ".card{background:#fafafa;border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin:20px 0}",
        ".evidence-card{background:#f8f9fa;border-left:4px solid #3949ab;padding:8px 12px;margin:6px 0;font-family:monospace;font-size:12px}",
        ".verified{color:#2e7d32;font-weight:bold}.incorrect{color:#c62828;font-weight:bold}.needs-review{color:#e65100;font-weight:bold}",
        ".hallucination{background:#ffebee}.best-answer{background:#e8f5e9}",
        ".cover{text-align:center;padding:60px 0;border-bottom:2px solid #c5cae9;margin-bottom:40px}",
        "pre{background:#f5f5f5;padding:12px;border-radius:4px;overflow-x:auto;font-size:12px;white-space:pre-wrap}",
        "</style></head><body>",
        "<section class=\"cover\">",
        "<h1>Security Audit Report</h1>",
        f"<p><strong>{_h(project.get('name') or project.get('id') or '')}</strong></p>",
        f"<p>Repository: {_h(project.get('repo_url') or project.get('local_path') or '')}</p>",
        f"<p>Export date: {_h(generated_at)}</p>",
        f"<p>Auditor: {_h(data.get('auditor_name') or 'Unknown')}</p>",
        f"<p>Project session ID: {_h(project.get('id') or '')}</p>",
        f"<p>Chat session IDs: {_h(', '.join(_chat_session_ids(data['chat_messages'])) or 'None recorded')}</p>",
        f"<p>Question count: {len(qa_pairs)} | Models: {_h(', '.join(model_names) or 'None recorded')}</p>",
        "</section>",
        "<h2>Security Wiki Context</h2>",
        f"<p><strong>{_h(EVIDENCE_DISCLAIMER)}</strong></p>",
    ]
    if data["wiki_pages"]:
        for page in data["wiki_pages"]:
            content = page.get("content_markdown") or ""
            parts.append(f"<h3>{_h(page.get('title') or 'Security Wiki')}</h3>")
            if content.strip().startswith("{"):
                try:
                    wiki = SecurityWikiSchema.model_validate_json(content)
                    parts.append(_wiki_tables_html(wiki))
                except ValidationError:
                    parts.append(f"<pre>{_h(content)}</pre>")
            else:
                parts.append(f"<pre>{_h(wiki_storage_to_markdown(content))}</pre>")
    else:
        parts.append("<p>No wiki generated for this project</p>")
    parts.append("<h2>Audit Questions &amp; Answers</h2>")
    if not qa_pairs:
        parts.append("<div class=\"card\"><h3>Auditor Verification</h3><p>No chat answers recorded.</p><h3>Evaluation Scores</h3><p>No chat-linked evaluations recorded.</p></div>")
    for index, item in enumerate(qa_pairs, start=1):
        answer = item["answer"]
        verification = _verification_for(answer.get("id"), data["verifications"])
        evaluation = _evaluation_for_chat(answer.get("id"), data["evaluations"])
        parts.extend(
            [
                "<div class=\"card\">",
                f"<h3>Q{index}: {_h(item['question'].get('content') or 'Untitled question')}{_h(_attempt_suffix(item))}</h3>",
                f"<p><strong>Asked:</strong> {_h(item['question'].get('created_at') or '')}</p>",
                f"<p><strong>Model:</strong> {_h((answer.get('model_provider') or '') + ' / ' + (answer.get('model_name') or ''))}</p>",
                f"<p><strong>Answer:</strong> {_h(answer.get('content') or '')}</p>",
                f"<p><strong>Auditor Verification:</strong> {_h((verification or {}).get('verdict') or 'Not verified')}</p>",
                f"<p><strong>Evaluation Scores:</strong> {_h(_evaluation_human_score(evaluation) if evaluation else 'Not yet scored')}</p>",
            ]
        )
        for evidence in _json_list(answer.get("evidence_json")):
            parts.append(
                f"<div class=\"evidence-card\">{_h(evidence.get('file_path') or '')}:"
                f"{_h(str(evidence.get('start_line') or ''))}-{_h(str(evidence.get('end_line') or ''))}<br>"
                f"{_h(_shorten(evidence.get('code_snippet') or '', 260))}</div>"
            )
        parts.append("</div>")
    parts.append("<h2>Model Comparison Table</h2>")
    for question, rows in _evaluations_by_question(data["evaluations"]).items():
        best = max((row.get("correctness_score") for row in rows if row.get("correctness_score") is not None), default=None)
        parts.append(f"<h3>{_h(question)}</h3><table><tr><th>Model</th><th>Answer</th><th>Correctness</th><th>Evidence Quality</th><th>Hallucination</th><th>Notes</th></tr>")
        for row in rows:
            classes = []
            if best is not None and row.get("correctness_score") == best:
                classes.append("best-answer")
            if row.get("hallucination_flag") == 1:
                classes.append("hallucination")
            parts.append(
                f"<tr class=\"{' '.join(classes)}\"><td>{_h((row.get('model_provider') or '') + ' / ' + (row.get('model_name') or ''))}</td>"
                f"<td>{_h(_shorten(row.get('answer_text') or '', 100))}</td><td>{_h(str(row.get('correctness_score') if row.get('correctness_score') is not None else ''))}</td>"
                f"<td>{_h(str(row.get('evidence_quality_score') if row.get('evidence_quality_score') is not None else ''))}</td>"
                f"<td>{_h(_hallucination_label(row))}</td><td>{_h(row.get('evaluator_comment') or row.get('human_comment') or '')}</td></tr>"
            )
        parts.append("</table>")
    parts.append("<h2>Overall Scores</h2>")
    parts.append(_overall_scores_html(data["evaluations"]))
    parts.extend(
        [
            "<h2>System Limitations</h2>",
            "<ul>",
            "<li>Parser: tree-sitter for Java/Go, AST for Python, heuristic fallback for other languages</li>",
            "<li>Retrieval: semantic vector + keyword hybrid; may miss obfuscated permission checks</li>",
            "<li>All answers are grounded in retrieved evidence but not formally verified</li>",
            "<li>Human evaluation scores represent auditor judgment, not ground truth</li>",
            "</ul></body></html>",
        ]
    )
    return "".join(parts)


def _comparison_csv_export(data: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
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
    )
    for index, item in enumerate(data["evaluations"], start=1):
        evidence_count = len(_json_list(item.get("evidence_json")))
        answer = item.get("answer_text") or ""
        writer.writerow(
            [
                index,
                item.get("question") or "",
                item.get("model_provider") or "",
                item.get("model_name") or "",
                evidence_count,
                len(answer),
                item.get("correctness_score") if item.get("correctness_score") is not None else item.get("correct_file_path"),
                item.get("evidence_quality_score") if item.get("evidence_quality_score") is not None else item.get("correct_code_block"),
                "" if item.get("hallucination_flag") is None else bool(item.get("hallucination_flag")),
                item.get("evaluator_comment") or item.get("human_comment") or "",
            ]
        )
    return output.getvalue()


def _qa_pairs(messages: list[dict]) -> list[dict]:
    pairs = []
    pending_question = None
    for message in messages:
        if message.get("role") == "user":
            pending_question = message
        elif message.get("role") == "assistant" and pending_question:
            pairs.append({"question": pending_question, "answer": message})
            pending_question = None
    return pairs


def _qa_pairs_with_attempts(messages: list[dict]) -> list[dict]:
    pairs = _qa_pairs(messages)
    totals: dict[str, int] = {}
    seen: dict[str, int] = {}
    for item in pairs:
        key = _question_key(item["question"].get("content"))
        totals[key] = totals.get(key, 0) + 1
    for item in pairs:
        key = _question_key(item["question"].get("content"))
        seen[key] = seen.get(key, 0) + 1
        item["attempt"] = seen[key]
        item["attempt_total"] = totals[key]
    return pairs


def _question_key(question: str | None) -> str:
    return " ".join((question or "Untitled question").lower().split())


def _attempt_suffix(item: dict) -> str:
    if item.get("attempt_total", 1) <= 1:
        return ""
    return f" (Attempt {item['attempt']} of {item['attempt_total']})"


def _chat_session_ids(messages: list[dict]) -> list[str]:
    return list(dict.fromkeys(item["session_id"] for item in messages if item.get("session_id")))


def _deduplicate_wiki_pages(pages: list[dict]) -> list[dict]:
    deduplicated = []
    seen = set()
    for page in pages:
        key = page.get("module_id") or page.get("slug") or page.get("title") or page.get("id")
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(page)
    return deduplicated

def _json_list(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _evaluation_human_score(item: dict) -> str:
    correctness = item.get("correctness_score")
    evidence_quality = item.get("evidence_quality_score")
    if correctness is not None or evidence_quality is not None:
        return f"Correctness={correctness if correctness is not None else 'n/a'}, Evidence={evidence_quality if evidence_quality is not None else 'n/a'}"
    legacy = [item.get("correct_file_path"), item.get("correct_code_block"), item.get("explanation_quality"), item.get("completeness"), item.get("usefulness")]
    if any(value is not None for value in legacy):
        return "Legacy scores: " + ", ".join(str(value) if value is not None else "n/a" for value in legacy)
    return "Not yet scored"


def _chat_human_score(answer: dict, evaluations: list[dict]) -> str:
    matching = [item for item in evaluations if item.get("chat_message_id") == answer.get("id")]
    return _evaluation_human_score(matching[0]) if matching else "Not yet scored"


def _verification_for(target_id: str | None, verifications: list[dict]) -> dict | None:
    if not target_id:
        return None
    for item in verifications:
        if item.get("target_type") == "chat_message" and item.get("target_id") == target_id:
            return item
    return None


def _evaluation_for_chat(chat_message_id: str | None, evaluations: list[dict]) -> dict | None:
    if not chat_message_id:
        return None
    for item in evaluations:
        if item.get("chat_message_id") == chat_message_id:
            return item
    return None


def _hallucination_label(evaluation: dict | None) -> str:
    if not evaluation or evaluation.get("hallucination_flag") is None:
        return "Not scored"
    return "Yes" if evaluation.get("hallucination_flag") else "No"


def _evaluations_by_question(evaluations: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in evaluations:
        grouped.setdefault(item.get("question") or "Untitled question", []).append(item)
    return grouped


def _overall_scores_html(evaluations: list[dict]) -> str:
    by_model: dict[str, list[dict]] = {}
    for item in evaluations:
        key = f"{item.get('model_provider') or ''} / {item.get('model_name') or ''}"
        by_model.setdefault(key, []).append(item)
    rows = ["<table><tr><th>Model</th><th>Avg Correctness</th><th>Avg Evidence Quality</th><th>Hallucinations</th><th>Evaluated</th></tr>"]
    for model, items in sorted(by_model.items()):
        correctness = _avg([item.get("correctness_score") for item in items])
        evidence = _avg([item.get("evidence_quality_score") for item in items])
        hallucinations = sum(1 for item in items if item.get("hallucination_flag") == 1)
        rows.append(f"<tr><td>{_h(model)}</td><td>{correctness}</td><td>{evidence}</td><td>{hallucinations}</td><td>{len(items)}</td></tr>")
    if not by_model:
        rows.append("<tr><td colspan=\"5\">No evaluations recorded. Scores must be entered manually in the audit workspace.</td></tr>")
    rows.append("</table>")
    return "".join(rows)


def _avg(values: list[object]) -> str:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return "n/a"
    return f"{sum(numbers) / len(numbers):.2f}"


def _wiki_tables_html(wiki: SecurityWikiSchema) -> str:
    rows = [f"<p>{_h(wiki.module_overview)}</p>", "<h3>Entry Points</h3><table><tr><th>Name</th><th>File</th><th>Lines</th><th>Description</th></tr>"]
    for item in wiki.entry_points:
        rows.append(f"<tr><td>{_h(item.name)}</td><td>{_h(item.file_path)}</td><td>{item.start_line}-{item.end_line}</td><td>{_h(item.description)}</td></tr>")
    rows.append("</table><h3>Access Control Matrix</h3><table><tr><th>Caller</th><th>Permission Check</th><th>File</th><th>Start Line</th></tr>")
    for item in wiki.access_control_matrix:
        rows.append(f"<tr><td>{_h(item.caller)}</td><td>{_h(item.permission_check)}</td><td>{_h(item.file_path)}</td><td>{_h(str(item.start_line or ''))}</td></tr>")
    rows.append("</table><h3>Limitations</h3>")
    rows.append(f"<pre>{_h(wiki.limitations)}</pre>")
    return "".join(rows)


def _h(value: object) -> str:
    return html.escape(str(value), quote=True)


def _shorten(value: str, limit: int = 180) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "security_codewiki"
