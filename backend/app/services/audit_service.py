# READ SUMMARY: This module orchestrates wiki generation, evidence-backed chat, model comparison, verification, scoring, and exports.
# CHANGED: Unified Ask/Compare retrieval packages, added display statuses, and handled provider timeouts without aborting comparisons.
import csv
import io
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pydantic import ValidationError

from app.core.config import get_settings
from app.db.database import db
from app.db.schemas import ChatAnswer, ChatRequest, CompareRequest, DISPLAY_STATUS_MAP, EvaluationScoreRequest, SecurityWikiSchema, VerificationRequest, WikiGenerateRequest, display_status_for
from app.services.llm import generate_structured_security_wiki, provider_for, security_wiki_system_prompt
from app.services.project_service import evidence_to_prompt, now, retrieve_evidence, retrieve_evidence_package, retrieve_wiki_context
from app.services.vector_index import code_chunk_exists, index_wiki_page


logger = logging.getLogger(__name__)


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
Reference only retrieved Evidence IDs in evidence_refs.
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
    evidence = retrieve_evidence(project_id, request.module_path, request.module_path, limit=12)
    evidence_prompt = evidence_to_prompt(evidence)
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
                "If evidence is missing, state that in limitations.\n\n"
                f"Target module: {request.module_path}\n\n{evidence_prompt}"
            ),
        },
    ]
    wiki, raw_response, validation_status = await generate_structured_security_wiki(provider, messages, model)
    validate_wiki_chunk_ids(project_id, wiki)
    wiki_json = wiki.model_dump_json()
    content_markdown = render_wiki_to_markdown(wiki)
    page_id = str(uuid.uuid4())
    timestamp = now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO wiki_pages (id, project_id, module_id, title, slug, content_markdown, wiki_schema_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (page_id, project_id, request.module_path, "Security Wiki", "security-wiki", wiki_json, "1.0", timestamp, timestamp),
        )
    index_wiki_page(project_id, page_id, request.module_path, "Security Wiki", content_markdown)
    return {
        "wiki_page_id": page_id,
        "content_markdown": content_markdown,
        "evidence": evidence,
        "provider": request.provider,
        "model": model,
        "validation_status": validation_status,
        "raw_model_response": raw_response,
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


def validate_wiki_chunk_ids(project_id: str, wiki: SecurityWikiSchema) -> None:
    for item in [*wiki.entry_points, *wiki.access_control_matrix]:
        if item.chunk_id and not code_chunk_exists(project_id, item.chunk_id):
            logger.warning("Wiki referenced unknown code chunk_id %s in project %s; clearing reference.", item.chunk_id, project_id)
            item.chunk_id = None


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
    # ASK RETRIEVAL TRACE:
    # Retrieval function: retrieve_evidence_package
    # Parameters: query text=request.question, project_id=project_id, top_k=8, filters=None
    # Retrieval happens once for the request and returns both source chunks and wiki chunks.
    # Wiki context is fetched from the same shared package used by Compare.
    with db() as connection:
        evidence_package = retrieve_evidence_package(project_id, request.question, 8, connection)
    evidence = evidence_package["source_chunks"]
    wiki_context = evidence_package["wiki_chunks"]
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
        result = await safe_generate(
            provider,
            [
                {"role": "system", "content": CHAT_JSON_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Question: {request.question}\n\n"
                        "A. Source-code evidence. Use this as proof for all claims:\n"
                        f"{evidence_to_prompt(evidence)}\n\n"
                        "B. Generated wiki context. Use this only for orientation, not as proof:\n"
                        f"{wiki_context_to_prompt(wiki_context)}"
                    ),
                },
            ],
            model,
        )
        raw_model_response = result.get("content") or ""
        if _is_timeout_result(result):
            content = "Model did not respond within the time limit."
            validation_status = "timeout"
            parsed_answer_json = None
            response_evidence = []
        else:
            parsed, validation_status, invalid_refs = parse_chat_answer(raw_model_response, evidence)
            if parsed:
                limitations = list(parsed.limitations)
                if invalid_refs:
                    limitations.append(f"Model referenced invalid evidence IDs that were dropped: {', '.join(invalid_refs)}")
                content = parsed.answer
                if limitations:
                    content = f"{content}\n\nLimitations:\n" + "\n".join(f"- {item}" for item in limitations)
                parsed_payload = parsed.model_dump()
                parsed_payload["limitations"] = limitations
                parsed_payload["valid_evidence_refs"] = [ref for ref in parsed.evidence_refs if ref in {item["chunk_id"] for item in evidence}]
                parsed_answer_json = json.dumps(parsed_payload)
                response_evidence = _evidence_for_refs(evidence, parsed_payload["valid_evidence_refs"])
            else:
                content = raw_model_response or "Not verified from the available source-code evidence."
                parsed_answer_json = None
                response_evidence = evidence
    with db() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages
            (id, session_id, role, content, evidence_json, raw_model_response, parsed_answer_json, validation_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (answer_id, session_id, "assistant", content, json.dumps(response_evidence), raw_model_response, parsed_answer_json, validation_status, now()),
        )
    return {
        "session_id": session_id,
        "message_id": answer_id,
        "answer": content,
        "evidence": response_evidence,
        "wiki_context": wiki_context,
        "context_used": "raw code + wiki context" if wiki_context else "raw code evidence only",
        "validation_status": validation_status,
        "display_status": display_status_for(validation_status),
        "provider": request.provider,
        "model": model,
    }


def parse_chat_answer(raw_content: str, evidence: list[dict]) -> tuple[ChatAnswer | None, str, list[str]]:
    valid_ids = {item["chunk_id"] for item in evidence}
    try:
        payload = json.loads(_extract_json_object(raw_content))
        parsed = ChatAnswer.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, ValueError):
        return None, "invalid_json_fallback", []
    invalid_refs = [ref for ref in parsed.evidence_refs if ref not in valid_ids]
    if invalid_refs:
        parsed.evidence_refs = [ref for ref in parsed.evidence_refs if ref in valid_ids]
        return parsed, "valid_with_dropped_invalid_evidence_refs", invalid_refs
    return parsed, "valid_json", []


def _extract_json_object(raw_content: str) -> str:
    stripped = raw_content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return stripped[start : end + 1]


def _evidence_for_refs(evidence: list[dict], refs: list[str]) -> list[dict]:
    if not refs:
        return evidence
    ref_set = set(refs)
    filtered = [item for item in evidence if item["chunk_id"] in ref_set]
    return filtered or evidence


async def compare_models(project_id: str, request: CompareRequest) -> dict:
    # COMPARE RETRIEVAL TRACE:
    # Retrieval function: retrieve_evidence_package
    # Parameters: query text=request.question, project_id=project_id, top_k=8, filters=None
    # Retrieval happens once and the same evidence package is passed to every selected provider.
    # Wiki context is fetched from the same shared package used by Ask.
    with db() as connection:
        evidence_package = retrieve_evidence_package(project_id, request.question, 8, connection)
    evidence = evidence_package["source_chunks"]
    wiki_context = evidence_package["wiki_chunks"]
    results = []
    for provider_name in request.providers:
        provider, default_model = provider_for(provider_name)
        model = default_model
        started = time.perf_counter()
        if evidence:
            result = await safe_generate(
                provider,
                [
                    {"role": "system", "content": CHAT_JSON_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {request.question}\n\n"
                            "A. Source-code evidence. Use this as proof for all claims:\n"
                            f"{evidence_to_prompt(evidence)}\n\n"
                            "B. Generated wiki context. Use this only for orientation, not as proof:\n"
                            f"{wiki_context_to_prompt(wiki_context)}"
                        ),
                    },
                ],
                model,
            )
            raw_answer = result.get("content") or "Not verified from the available source-code evidence."
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
            else:
                parsed, validation_status, invalid_refs = parse_chat_answer(raw_answer, evidence)
                if parsed:
                    limitations = list(parsed.limitations)
                    if invalid_refs:
                        limitations.append(f"Model referenced invalid evidence IDs that were dropped: {', '.join(invalid_refs)}")
                    answer = parsed.answer
                    if limitations:
                        answer = f"{answer}\n\nLimitations:\n" + "\n".join(f"- {item}" for item in limitations)
                    parsed_payload = parsed.model_dump()
                    parsed_payload["limitations"] = limitations
                    parsed_payload["valid_evidence_refs"] = [ref for ref in parsed.evidence_refs if ref in {item["chunk_id"] for item in evidence}]
                    parsed_answer_json = json.dumps(parsed_payload)
                else:
                    answer = raw_answer
                    parsed_answer_json = None
        else:
            answer = "Not verified from the available source-code evidence."
            parsed_answer_json = None
            validation_status = "no_evidence"
        latency_ms = int((time.perf_counter() - started) * 1000)
        evaluation_id = str(uuid.uuid4())
        with db() as connection:
            connection.execute(
                """
                INSERT INTO evaluations
                (id, project_id, module_path, question, chat_message_id, model_provider, model_name, answer_text,
                 parsed_answer_json, evidence_json, wiki_context_json, validation_status, latency_ms, estimated_cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(evidence),
                    json.dumps(wiki_context),
                    validation_status,
                    latency_ms,
                    0.0,
                    now(),
                ),
            )
        results.append(
            {
                "evaluation_id": evaluation_id,
                "provider": provider_name,
                "model": model,
                "answer": answer,
                "latency_ms": latency_ms,
                "validation_status": validation_status,
                "display_status": display_status_for(validation_status),
            }
        )
    return {"question": request.question, "evidence": evidence, "wiki_context": wiki_context, "results": results}


async def safe_generate(provider, messages: list[dict], model: str) -> dict:
    try:
        return await provider.generate(messages, model)
    except TimeoutError:
        return timeout_response()
    except Exception as exc:
        return {
            "content": f"Model provider failed before returning a response: {exc}\n\nNot verified from the available source-code evidence.",
            "raw": {"error": "unhandled_provider_exception", "detail": str(exc)},
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


def score_evaluation(evaluation_id: str, request: EvaluationScoreRequest) -> dict:
    payload = request.model_dump()
    notes = payload["notes"] if payload["notes"] is not None else payload["evaluator_comment"]
    hallucination = payload["hallucination"] if payload["hallucination"] is not None else payload["hallucination_flag"]
    with db() as connection:
        connection.execute(
            """
            UPDATE evaluations
            SET correctness_score = ?,
                evidence_quality_score = ?,
                correct_file_path = ?,
                correct_code_block = ?,
                explanation_quality = ?,
                completeness = ?,
                hallucination_flag = ?,
                usefulness = ?,
                evaluator_comment = ?,
                human_comment = ?
            WHERE id = ?
            """,
            (
                payload["correctness"],
                payload["evidence_quality"],
                payload["correct_file_path"],
                payload["correct_code_block"],
                payload["explanation_quality"],
                payload["completeness"],
                None if hallucination is None else int(hallucination),
                payload["usefulness"],
                notes,
                notes,
                evaluation_id,
            ),
        )
        row = connection.execute("SELECT * FROM evaluations WHERE id = ?", (evaluation_id,)).fetchone()
    return dict(row) if row else {"id": evaluation_id, "error": "Evaluation not found"}


def export_project(project_id: str, export_format: str) -> tuple[str, str, str]:
    data = _export_data(project_id)
    project = data["project"]
    project_name = _safe_filename(project.get("name") or project_id)
    if export_format == "json":
        return "application/json", f"{project_name}_audit_report.json", json.dumps(data, indent=2)
    if export_format == "csv":
        return "text/csv", f"{project_name}_audit_report.csv", _comparison_csv_export(data)
    return "text/markdown", f"{project_name}_audit_report.md", _markdown_export(data)


def _export_data(project_id: str) -> dict:
    with db() as connection:
        project_row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not project_row:
            raise ValueError("Project not found")
        project = dict(project_row)
        wiki_pages = [dict(row) for row in connection.execute("SELECT * FROM wiki_pages WHERE project_id = ?", (project_id,)).fetchall()]
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
        verifications = [dict(row) for row in connection.execute("SELECT * FROM verifications ORDER BY created_at DESC").fetchall()]
    return {
        "project": project,
        "wiki_pages": wiki_pages,
        "chat_messages": chat_messages,
        "evaluations": evaluations,
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
        "## 2. Security Wiki",
    ]
    for page in data["wiki_pages"]:
        lines.extend(["", f"### {page['title']}", "", wiki_storage_to_markdown(page.get("content_markdown") or "")])
    if not data["wiki_pages"]:
        lines.extend(["", "No Security Wiki pages have been generated yet."])

    lines.extend(["", "## 3. Audit Questions and Answers"])
    for index, item in enumerate(_qa_pairs(data["chat_messages"]), start=1):
        answer = item["answer"]
        evidence = _json_list(answer.get("evidence_json"))
        lines.extend(
            [
                "",
                f"### Q{index}: {item['question'].get('content') or 'Untitled question'}",
                "",
                f"**Model:** {answer.get('model_provider') or ''} / {answer.get('model_name') or ''}",
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
        lines.extend(["", f"**Human Score:** {_chat_human_score(answer, data['evaluations'])}"])

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


def _shorten(value: str, limit: int = 180) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."


def _safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return cleaned.strip("_") or "security_codewiki"
