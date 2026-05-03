import csv
import io
import json
import time
import uuid

from app.core.config import get_settings
from app.db.database import db
from app.db.schemas import ChatRequest, CompareRequest, VerificationRequest, WikiGenerateRequest
from app.services.llm import provider_for
from app.services.project_service import evidence_to_prompt, now, retrieve_evidence, retrieve_wiki_context
from app.services.vector_index import index_wiki_page


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
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Generate a structured Security Wiki for the selected target module using only this evidence. "
                "Include sections: Module Security Overview, Public Entry Points, Horizontal Access-Control Matrix, "
                "Vertical Helper Analysis, Requirement-to-Code Traces, Evidence Blocks, Human Auditor Notes. "
                "If evidence is missing, include a Needs Review section.\n\n"
                f"Target module: {request.module_path}\n\n{evidence_prompt}"
            ),
        },
    ]
    result = await safe_generate(provider, messages, model)
    content = result.get("content") or "Not verified from the available evidence."
    page_id = str(uuid.uuid4())
    timestamp = now()
    with db() as connection:
        connection.execute(
            """
            INSERT INTO wiki_pages (id, project_id, module_id, title, slug, content_markdown, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (page_id, project_id, request.module_path, "Security Wiki", "security-wiki", content, timestamp, timestamp),
        )
    index_wiki_page(project_id, page_id, request.module_path, "Security Wiki", content)
    return {"wiki_page_id": page_id, "content_markdown": content, "evidence": evidence, "provider": request.provider, "model": model}


def list_wiki_pages(project_id: str) -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM wiki_pages WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)).fetchall()
    return [dict(row) for row in rows]


async def chat(project_id: str, request: ChatRequest) -> dict:
    evidence = retrieve_evidence(project_id, request.question, request.module_id)
    wiki_context = retrieve_wiki_context(project_id, request.question, request.module_id)
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
    else:
        result = await safe_generate(
            provider,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
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
        content = result.get("content") or "Not verified from the available source-code evidence."
    with db() as connection:
        connection.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (answer_id, session_id, "assistant", content, json.dumps(evidence), now()),
        )
    return {
        "session_id": session_id,
        "message_id": answer_id,
        "answer": content,
        "evidence": evidence,
        "wiki_context": wiki_context,
        "context_used": "raw code + wiki context" if wiki_context else "raw code evidence only",
        "provider": request.provider,
        "model": model,
    }


async def compare_models(project_id: str, request: CompareRequest) -> dict:
    evidence = retrieve_evidence(project_id, request.question, request.module_id)
    wiki_context = retrieve_wiki_context(project_id, request.question, request.module_id)
    results = []
    for provider_name in request.providers:
        provider, default_model = provider_for(provider_name)
        model = default_model
        started = time.perf_counter()
        if evidence:
            result = await safe_generate(
                provider,
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
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
            answer = result.get("content") or "Not verified from the available source-code evidence."
        else:
            answer = "Not verified from the available source-code evidence."
        latency_ms = int((time.perf_counter() - started) * 1000)
        evaluation_id = str(uuid.uuid4())
        with db() as connection:
            connection.execute(
                """
                INSERT INTO evaluations (id, chat_message_id, model_provider, model_name, latency_ms, estimated_cost, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (evaluation_id, None, provider_name, model, latency_ms, 0.0, now()),
            )
        results.append({"evaluation_id": evaluation_id, "provider": provider_name, "model": model, "answer": answer, "latency_ms": latency_ms})
    return {"question": request.question, "evidence": evidence, "wiki_context": wiki_context, "results": results}


async def safe_generate(provider, messages: list[dict], model: str) -> dict:
    try:
        return await provider.generate(messages, model)
    except Exception as exc:
        return {
            "content": f"Model provider failed before returning a response: {exc}\n\nNot verified from the available source-code evidence.",
            "raw": {"error": "unhandled_provider_exception", "detail": str(exc)},
            "ok": False,
        }


def verify(request: VerificationRequest) -> dict:
    verification_id = str(uuid.uuid4())
    with db() as connection:
        connection.execute(
            "INSERT INTO verifications (id, target_type, target_id, verdict, human_comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (verification_id, request.target_type, request.target_id, request.verdict, request.human_comment, now()),
        )
    return {"id": verification_id, **request.model_dump(), "created_at": now()}


def export_project(project_id: str, export_format: str) -> tuple[str, str, str]:
    data = _export_data(project_id)
    if export_format == "json":
        return "application/json", f"security-codewiki-{project_id}.json", json.dumps(data, indent=2)
    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["type", "id", "title_or_role", "content", "evidence"])
        for page in data["wiki_pages"]:
            writer.writerow(["wiki_page", page["id"], page["title"], page["content_markdown"], ""])
        for message in data["chat_messages"]:
            writer.writerow(["chat_message", message["id"], message["role"], message["content"], message.get("evidence_json") or ""])
        return "text/csv", f"security-codewiki-{project_id}.csv", output.getvalue()
    return "text/markdown", f"security-codewiki-{project_id}.md", _markdown_export(data)


def _export_data(project_id: str) -> dict:
    with db() as connection:
        project = dict(connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        wiki_pages = [dict(row) for row in connection.execute("SELECT * FROM wiki_pages WHERE project_id = ?", (project_id,)).fetchall()]
        chat_messages = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.* FROM chat_messages m
                JOIN chat_sessions s ON s.id = m.session_id
                WHERE s.project_id = ?
                ORDER BY m.created_at
                """,
                (project_id,),
            ).fetchall()
        ]
        evaluations = [dict(row) for row in connection.execute("SELECT * FROM evaluations ORDER BY created_at DESC").fetchall()]
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
    lines = [
        f"# Security CodeWiki Report: {project['name']}",
        "",
        f"- Project ID: {project['id']}",
        f"- Source Type: {project['source_type']}",
        f"- Repository URL: {project.get('repo_url') or ''}",
        f"- Commit Hash: {project.get('commit_hash') or ''}",
        f"- Security Goal: {project.get('security_goal') or ''}",
        "",
        "## Security Wiki Pages",
    ]
    for page in data["wiki_pages"]:
        lines.extend(["", f"### {page['title']}", "", page["content_markdown"]])
    lines.extend(["", "## Questions and Answers"])
    for message in data["chat_messages"]:
        lines.extend(["", f"### {message['role'].title()}", "", message["content"]])
    lines.extend(["", "## Verification Results"])
    for item in data["verifications"]:
        lines.append(f"- {item['target_type']} {item['target_id']}: {item['verdict']} {item.get('human_comment') or ''}")
    lines.extend(["", "## Limitations", "", "- Not verified from the available evidence means the system did not retrieve enough source evidence."])
    return "\n".join(lines)
