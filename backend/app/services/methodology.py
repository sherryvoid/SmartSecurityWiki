"""Frozen Phase 4.5 evaluation, provenance, and model-reference contracts."""
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone

from app.core.config import get_settings
from app.db.database import db
from app.services.project_service import get_project, now


def canonical_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_presentation_text_items(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return [str(value)]


def render_structured_answer_payload(payload: dict) -> str:
    """Lossless human-readable projection of every substantive structured answer field."""
    sections = [str(payload.get("answer") or "").strip()]
    if payload.get("access_control_summary"):
        sections.append("## Access-control summary\n\n" + str(payload["access_control_summary"]).strip())
    helper_chain = normalize_presentation_text_items(payload.get("helper_chain"))
    if helper_chain:
        sections.append("## Helper chain\n\n" + "\n".join(f"{index}. {item}" for index, item in enumerate(helper_chain, 1)))
    limitations = normalize_presentation_text_items(payload.get("limitations"))
    if limitations:
        sections.append("## Limitations\n\n" + "\n".join(f"- {item}" for item in limitations))
    return "\n\n".join(section for section in sections if section)


def freeze_wiki_context(chunks: list[dict]) -> dict:
    members = [{k: item.get(k) for k in ("chunk_id", "wiki_id", "title", "section", "source_focus")} for item in chunks]
    return {"ordered_wiki_chunk_ids": [item.get("chunk_id") for item in chunks], "shared_wiki_context_hash": canonical_hash(members)}


def evaluation_configuration(project_id: str, models: list[dict] | None = None) -> dict:
    settings = get_settings()
    project = get_project(project_id)
    try:
        app_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, check=False).stdout.strip() or "unavailable"
    except Exception:
        app_sha = "unavailable"
    selected_models = []
    with db() as connection:
        for selected in models or []:
            item = dict(selected)
            metadata = connection.execute("SELECT digest, metadata_json FROM model_runtime_metadata WHERE provider=? AND model=?", (item.get("provider"), item.get("model"))).fetchone()
            if metadata:
                item["digest"] = metadata["digest"]
            if item.get("provider") == "ollama":
                item.update({"reasoning": "enabled" if settings.ollama_think_enabled else "disabled", "temperature": 0.1, "top_p": None, "num_predict": settings.ollama_num_predict, "context_length": settings.ollama_context_length, "timeout_seconds": settings.ollama_timeout_seconds, "inference_options": {"think": settings.ollama_think_enabled}})
            elif item.get("provider") == "groq":
                item.update({"reasoning_effort": settings.groq_reasoning_effort or "provider_default", "reasoning_format": settings.groq_reasoning_format, "include_reasoning": settings.groq_include_reasoning, "temperature": "provider_default", "max_output_tokens": settings.groq_max_output_tokens, "timeout_seconds": settings.groq_timeout_seconds, "base_url": settings.groq_base_url})
            elif item.get("provider") == "openrouter":
                item.update({"temperature": 0.1, "max_output_tokens": settings.openrouter_max_output_tokens, "timeout_seconds": settings.openrouter_timeout_seconds, "base_url": settings.openrouter_base_url, "deployment_route": f"{settings.openrouter_model} accessed through OpenRouter"})
                if str(item.get("model") or "").lower() in {"openai/gpt-5.1", "gpt-5.1"}:
                    item["presentation_prompt_version"] = settings.gpt51_presentation_version
            selected_models.append(item)
    config = {
        "revision": settings.evaluation_config_revision,
        "security_codewiki_commit_sha": app_sha,
        "target_repository_url": project.get("repo_url"),
        "target_repository_commit_sha": project.get("commit_hash"),
        "embedding_model": f"{settings.embedding_provider}/{settings.embedding_model}",
        "primary_source_top_k": settings.primary_source_top_k,
        "wiki_context_top_k": settings.wiki_context_top_k,
        "selected_file_min_max": [settings.selected_file_min_chunks, settings.selected_file_max_chunks],
        "evidence_role_configuration": "phase4.4-role-coverage-v1",
        "prompt_version": settings.prompt_version,
        "prompt_serialization_version": settings.prompt_serialization_version,
        "wiki_prompt_version": settings.wiki_prompt_version,
        "model_provider_ids": selected_models,
        "ollama_think_flag": settings.ollama_think_enabled,
        "ollama_num_predict": settings.ollama_num_predict,
        "generation_parameters": {"temperature": 0.1},
        "timeouts_seconds": {"ollama": settings.ollama_timeout_seconds, "openai": settings.openai_timeout_seconds, "gemini": settings.gemini_timeout_seconds, "openrouter": settings.openrouter_timeout_seconds},
        "compare_source_limit": settings.compare_source_chunk_limit,
        "retrieval_scoring_configuration": "hybrid-vector-lexical-rescore-v4.4",
    }
    return {"evaluation_config": config, "evaluation_config_hash": canonical_hash(config)}


def persist_model_runtime_metadata(provider: str, model: str, metadata: dict) -> dict:
    digest = metadata.get("digest")
    payload_hash = canonical_hash(metadata)
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO model_runtime_metadata (provider,model,digest,metadata_json,metadata_hash,captured_at) VALUES (?,?,?,?,?,?)", (provider, model, digest, json.dumps(metadata, ensure_ascii=False, sort_keys=True), payload_hash, datetime.now(timezone.utc).isoformat()))
    return {"provider": provider, "model": model, "digest": digest, "metadata_hash": payload_hash, "metadata": metadata}


def persist_evaluation_baseline(name: str, project_id: str, phase: str, payload: dict) -> dict:
    """Insert-only named research snapshot. Existing names are immutable."""
    payload_hash = canonical_hash(payload)
    with db() as connection:
        try:
            connection.execute("INSERT INTO evaluation_baselines (name,project_id,phase,payload_json,payload_hash,created_at) VALUES (?,?,?,?,?,?)", (name, project_id, phase, json.dumps(payload, ensure_ascii=False, sort_keys=True), payload_hash, datetime.now(timezone.utc).isoformat()))
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValueError(f"Evaluation baseline '{name}' already exists and is immutable.") from exc
            raise
    return {"name": name, "project_id": project_id, "phase": phase, "payload_hash": payload_hash}


def validate_model_references(answer: str, evidence: list[dict], existence_searches: list[dict] | None = None) -> dict:
    existence_searches = existence_searches or []
    paths = {item.get("file_path") for item in evidence if item.get("file_path")}
    file_stems = {path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]: path for path in paths}
    chunks = {item.get("chunk_id") for item in evidence}
    symbols = {item.get("symbol_name") for item in evidence if item.get("symbol_name")}
    types = {item.get("class_name") for item in evidence if item.get("class_name")}
    types.update(file_stems)
    source_text = "\n".join(str(item.get("code_snippet") or item.get("code") or "") for item in evidence)
    source_identifiers = set(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", source_text))
    warnings = []
    references = []
    def add_reference(reference_type: str, raw: str, valid: bool, matched=None, reason=""):
        entry = {"reference_type": reference_type, "raw_text": raw, "normalized_text": raw.strip(), "validation_status": "valid" if valid else "invalid", "matched_evidence": matched or [], "reason": reason}
        references.append(entry)
        return entry
    file_refs = re.findall(r"(?im)^\s*FILE\s*:\s*([^\r\n]+)", answer)
    for claim in file_refs:
        claim = claim.strip()
        if claim.lower() in {"unknown", "unavailable", "not provided"}:
            continue
        matches = [item["chunk_id"] for item in evidence if item.get("file_path") == claim or item.get("file_path", "").replace("\\", "/").endswith("/" + claim)]
        add_reference("file", claim, bool(matches), matches, "Matched supplied file path." if matches else "No supplied file path matched exactly.")
        if not matches:
            warnings.append({"code": "invalid_model_file_reference", "claim": claim, "message": "The model file reference does not exactly match retrieved source evidence."})
    line_refs = re.findall(r"(?im)^\s*LINE\s*:\s*([^\r\n]+)", answer)
    for claim in line_refs:
        if claim.strip().lower() in {"unknown", "unavailable", "not provided"}:
            continue
        numbers = [int(n) for n in re.findall(r"\d+", claim)]
        matches = [item["chunk_id"] for item in evidence if numbers and item.get("start_line", 0) <= numbers[0] <= item.get("end_line", -1)]
        add_reference("line_range", claim.strip(), bool(matches), matches, "Line falls within supplied evidence." if matches else "Line is outside supplied evidence ranges.")
        if not matches:
            warnings.append({"code": "invalid_model_line_reference", "claim": claim.strip(), "message": "The model line reference is outside retrieved source ranges."})
    chunk_claims = re.findall(r"(?i)\bchunk(?:_id)?\s*[:#]\s*([\w:.-]+)", answer)
    evidence_claims = re.findall(r"(?i)\bEvidence\s+#?\s*(\d+)\b", answer)
    for claim in [*chunk_claims, *evidence_claims]:
        valid = claim in chunks or (claim.isdigit() and 1 <= int(claim) <= len(evidence))
        matched = [claim] if claim in chunks else ([evidence[int(claim) - 1]["chunk_id"]] if valid and claim.isdigit() else [])
        add_reference("chunk_id" if claim in chunks else "evidence_number", claim, valid, matched, "Matched supplied evidence identifier." if valid else "Identifier was not supplied.")
        if not valid:
            warnings.append({"code": "invalid_model_chunk_reference", "claim": claim, "message": "The model evidence/chunk reference was not supplied by the backend."})
    bracket_references = re.findall(r"(?i)(?<![A-Za-z0-9_])([EX])(\d+)(?![A-Za-z0-9_])", answer)
    valid_source_aliases = {f"E{index}" for index in range(1, len(evidence) + 1)}
    valid_existence_aliases = {f"X{index}" for index in range(1, len(existence_searches) + 1)}
    cited_source_aliases, referenced_existence_aliases = set(), set()
    for namespace, number in bracket_references:
        reference = f"{namespace.upper()}{number}"
        if namespace.upper() == "E":
            valid = reference in valid_source_aliases
            if valid:
                cited_source_aliases.add(reference)
            matched = [evidence[int(number) - 1]["chunk_id"]] if valid else []
            add_reference("source_evidence_alias", reference, valid, matched, "Matched supplied source evidence alias." if valid else "Source evidence alias was not supplied.")
            if not valid:
                warnings.append({"code": "invalid_model_source_evidence_reference", "claim": reference, "message": "The model source evidence reference was not supplied by the backend."})
        else:
            valid = reference in valid_existence_aliases
            if valid:
                referenced_existence_aliases.add(reference)
            add_reference("repository_existence_alias", reference, valid, [reference] if valid else [], "Matched supplied repository-existence metadata." if valid else "Repository-existence evidence alias was not supplied.")
            if not valid:
                warnings.append({"code": "invalid_model_existence_evidence_reference", "claim": reference, "message": "The model repository-existence evidence reference was not supplied by the backend."})
    identifier_pattern = r"\b[A-Z][A-Za-z0-9_$]*(?:Controller|Service|Config|Configuration|Manager|Filter|Issuer|Mapper|Bootstrap)\b"
    for claim in dict.fromkeys(re.findall(identifier_pattern, answer)):
        matches = [item["chunk_id"] for item in evidence if claim in {item.get("class_name"), (item.get("file_path") or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]}]
        source_matches = [item["chunk_id"] for item in evidence if re.search(rf"\b{re.escape(claim)}\b", str(item.get("code_snippet") or item.get("code") or ""))]
        matched = list(dict.fromkeys([*matches, *source_matches]))
        add_reference("type_or_class", claim, bool(matched), matched, "Matched supplied metadata or source text." if matched else "No supplied metadata or source identifier matched.")
        if not matched:
            warnings.append({"code": "invalid_model_symbol_reference", "claim": claim, "message": "The model type/class reference does not match supplied evidence metadata."})
    already_checked = {item["raw_text"] for item in references}
    for claim in dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z0-9_$]{5,}\b", answer)):
        if claim in already_checked or claim in types or claim in source_identifiers or claim.isupper():
            continue
        related = next((known for known in types if len(_common_prefix(claim, known)) >= min(6, max(3, len(known) // 2))), None)
        if related:
            add_reference("type_or_class", claim, False, [], f"No exact type match; resembles supplied type {related} but references are not rewritten.")
            warnings.append({"code": "invalid_model_symbol_reference", "claim": claim, "message": "The model type/class reference does not exactly match supplied evidence metadata."})
    for claim in dict.fromkeys(symbol for symbol in symbols if re.search(rf"\b{re.escape(symbol)}\b", answer)):
        matches = [item["chunk_id"] for item in evidence if item.get("symbol_name") == claim]
        add_reference("method_or_function", claim, True, matches, "Matched supplied symbol metadata.")
    status = "unavailable" if not references else ("invalid" if warnings else "valid")
    return {"backend_evidence_validation_status": "backend_attached_valid" if evidence else "no_source_evidence", "model_reference_validation_status": status, "model_reference_warnings": warnings, "typed_model_references": references, "source_evidence_cited_count": len(cited_source_aliases), "existence_evidence_referenced_count": len(referenced_existence_aliases), "referenced_existence_evidence_ids": sorted(referenced_existence_aliases)}


def _common_prefix(left: str, right: str) -> str:
    length = 0
    for a, b in zip(left.lower(), right.lower()):
        if a != b:
            break
        length += 1
    return left[:length]


def persist_formal_run(record: dict) -> str:
    run_id = record.get("run_id") or str(uuid.uuid4())
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO formal_runs (run_id, project_id, operation, question, timestamp, provider_model_json, answer_json, primary_evidence_json, wiki_context_json, execution_status, comparison_metadata_json, evaluation_config_hash, human_evaluation_id, human_evaluation_status, supplied_source_evidence_json, cited_source_evidence_json, supplied_source_package_hash, supplied_wiki_package_hash, evaluation_config_json,run_purpose,question_id,started_at,completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            run_id, record["project_id"], record["operation"], record.get("question", ""), record.get("timestamp") or now(), json.dumps(record.get("provider_model")), json.dumps(record.get("answer")), json.dumps(record.get("cited_source_evidence", record.get("primary_evidence", []))), json.dumps(record.get("wiki_context", [])), record.get("execution_status"), json.dumps(record.get("comparison_metadata", {})), record.get("evaluation_config_hash"), record.get("human_evaluation_id"), record.get("human_evaluation_status", "not_scored"), json.dumps(record.get("supplied_source_evidence", record.get("primary_evidence", []))), json.dumps(record.get("cited_source_evidence", record.get("primary_evidence", []))), record.get("supplied_source_package_hash"), record.get("supplied_wiki_package_hash"), json.dumps(record.get("evaluation_config", {})),record.get("run_purpose","development"),record.get("question_id"),record.get("started_at"),record.get("completed_at")))
    return run_id


def list_formal_runs(project_id: str) -> list[dict]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM formal_runs WHERE project_id = ? ORDER BY timestamp DESC", (project_id,)).fetchall()
        evaluations = {row["id"]: row["parsed_answer_json"] for row in connection.execute("SELECT id,parsed_answer_json FROM evaluations WHERE project_id=?", (project_id,)).fetchall()}
    restored = []
    for row in rows:
        item = dict(row)
        try:
            answers = json.loads(item.get("answer_json") or "null")
            if isinstance(answers, list):
                for answer in answers:
                    parsed_json = evaluations.get(answer.get("evaluation_id"))
                    if parsed_json:
                        parsed = json.loads(parsed_json)
                        full = render_structured_answer_payload(parsed)
                        answer["answer_preview"] = answer.get("answer", "")
                        answer["full_answer"] = full
                        answer["answer"] = full
                item["answer_json"] = json.dumps(answers)
        except (TypeError, json.JSONDecodeError):
            pass
        restored.append(item)
    return restored


def update_run_purpose(project_id: str, run_id: str, run_purpose: str, question_id: str | None = None) -> dict:
    with db() as connection:
        existing = connection.execute("SELECT run_id FROM formal_runs WHERE run_id=? AND project_id=?", (run_id, project_id)).fetchone()
        if not existing:
            raise ValueError("Run not found")
        connection.execute("UPDATE formal_runs SET run_purpose=?,question_id=? WHERE run_id=?", (run_purpose, question_id, run_id))
        connection.execute("UPDATE model_usage SET run_purpose=? WHERE run_id=?", (run_purpose, run_id))
    return {"run_id":run_id,"run_purpose":run_purpose,"question_id":question_id}
