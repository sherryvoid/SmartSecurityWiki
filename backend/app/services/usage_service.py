"""Provider-neutral model usage, prompt composition, and optional cost accounting."""
import json
import uuid
from datetime import datetime, timezone

from app.db.database import db
from app.core.config import get_settings
from app.services.methodology import canonical_hash
from app.services.project_service import compact_evidence_to_prompt


def _size(text: str) -> dict:
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8")), "token_count": max(1, (len(text) + 3) // 4) if text else 0, "token_count_type": "estimated"}


def _difference_size(serialized: str, content_parts: list[str]) -> dict:
    characters = len(serialized) - sum(len(value) for value in content_parts)
    utf8_bytes = len(serialized.encode("utf-8")) - sum(len(value.encode("utf-8")) for value in content_parts)
    safe_characters = max(0, characters)
    return {"characters": safe_characters, "utf8_bytes": max(0, utf8_bytes), "token_count": max(1, (safe_characters + 3) // 4) if safe_characters else 0, "token_count_type": "estimated"}


def measure_prompt_components(messages: list[dict], question: str, source_chunks: list[dict], wiki_chunks: list[dict], wiki_serializer) -> dict:
    source_serialized = compact_evidence_to_prompt(source_chunks)
    wiki_serialized = wiki_serializer(wiki_chunks)
    source_content = "".join(item.get("code_snippet") or "" for item in source_chunks)
    wiki_content = "".join(item.get("content") or "" for item in wiki_chunks)
    system_text = "\n".join(message.get("content", "") for message in messages if message.get("role") == "system")
    complete = "\n".join(message.get("content", "") for message in messages)
    output_instructions = ""
    for marker in ("Return only valid JSON with this shape:", "Write your answer clearly. Then on separate lines at the end:"):
        search_text = system_text if marker in system_text else complete
        position = search_text.find(marker)
        if position >= 0:
            output_instructions = search_text[position:]
            break
    system_instructions = system_text
    if output_instructions and output_instructions in system_text:
        system_instructions = system_text.replace(output_instructions, "", 1)
    attributed_chars = len(system_instructions) + len(output_instructions) + len(question) + len(source_serialized) + len(wiki_serialized)
    components = {
        "system_instructions": _size(system_instructions),
        "user_question": _size(question),
        "primary_source_content": _size(source_content),
        "primary_evidence_metadata_wrappers": _difference_size(source_serialized, [item.get("code_snippet") or "" for item in source_chunks]),
        "wiki_context_content": _size(wiki_content),
        "wiki_context_metadata_wrappers": _difference_size(wiki_serialized, [item.get("content") or "" for item in wiki_chunks]),
        "output_format_schema_instructions": _size(output_instructions),
        "provider_specific_wrapper": _size(""),
        "additional_prompt_material": _difference_size(complete, [system_instructions, output_instructions, question, source_serialized, wiki_serialized]),
    }
    components["total_serialized_messages"] = _size(complete)
    components["supplied_source_blocks"] = len(source_chunks)
    components["supplied_wiki_blocks"] = len(wiki_chunks)
    return components


def normalize_usage(provider: str, raw: dict | None) -> dict:
    raw = raw or {}
    if provider == "ollama":
        input_tokens, output_tokens = raw.get("prompt_eval_count"), raw.get("eval_count")
        return {"provider_reported_input_tokens": input_tokens, "provider_reported_output_tokens": output_tokens, "provider_reported_total_tokens": (input_tokens + output_tokens) if input_tokens is not None and output_tokens is not None else None, "provider_reported_cached_input_tokens": None, "provider_reported_reasoning_tokens": None, "provider_reported_thinking_tokens": raw.get("thinking_tokens"), "usage_source": "provider_reported" if input_tokens is not None or output_tokens is not None else "unavailable", "load_duration_ms": _ns_ms(raw.get("load_duration")), "prompt_eval_duration_ms": _ns_ms(raw.get("prompt_eval_duration")), "generation_duration_ms": _ns_ms(raw.get("eval_duration")), "native_usage": raw}
    if provider == "gemini":
        value = lambda camel, snake: raw.get(camel) if camel in raw else raw.get(snake)
        input_tokens = value("promptTokenCount", "prompt_token_count")
        output_tokens = value("candidatesTokenCount", "candidates_token_count")
        total_tokens = value("totalTokenCount", "total_token_count")
        cached_tokens = value("cachedContentTokenCount", "cached_content_token_count")
        thought_tokens = value("thoughtsTokenCount", "thoughts_token_count")
        available = any(v is not None for v in (input_tokens, output_tokens, total_tokens, cached_tokens, thought_tokens))
        return {"provider_reported_input_tokens": input_tokens, "provider_reported_output_tokens": output_tokens, "provider_reported_total_tokens": total_tokens, "provider_reported_cached_input_tokens": cached_tokens, "provider_reported_reasoning_tokens": thought_tokens, "provider_reported_thinking_tokens": thought_tokens, "usage_source": "provider_reported" if available else "unavailable", "load_duration_ms": None, "prompt_eval_duration_ms": None, "generation_duration_ms": None, "native_usage": raw}
    if provider in {"openai", "openrouter"}:
        prompt_details, output_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}, raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
        reported_cost = raw.get("cost") if provider == "openrouter" else None
        try:
            reported_cost = float(reported_cost) if reported_cost is not None else None
        except (TypeError, ValueError):
            reported_cost = None
        return {"provider_reported_input_tokens": raw.get("prompt_tokens", raw.get("input_tokens")), "provider_reported_output_tokens": raw.get("completion_tokens", raw.get("output_tokens")), "provider_reported_total_tokens": raw.get("total_tokens"), "provider_reported_cached_input_tokens": prompt_details.get("cached_tokens"), "provider_reported_reasoning_tokens": output_details.get("reasoning_tokens"), "provider_reported_thinking_tokens": None, "provider_reported_cost": reported_cost, "usage_source": "provider_reported" if raw else "unavailable", "load_duration_ms": None, "prompt_eval_duration_ms": None, "generation_duration_ms": None, "native_usage": raw}
    if provider == "groq":
        prompt_details = raw.get("prompt_tokens_details") or {}
        completion_details = raw.get("completion_tokens_details") or {}
        input_tokens, output_tokens, total_tokens = raw.get("prompt_tokens"), raw.get("completion_tokens"), raw.get("total_tokens")
        available = any(value is not None for value in (input_tokens, output_tokens, total_tokens))
        seconds_ms = lambda value: None if value is None else value * 1000
        return {"provider_reported_input_tokens": input_tokens, "provider_reported_output_tokens": output_tokens, "provider_reported_total_tokens": total_tokens, "provider_reported_cached_input_tokens": prompt_details.get("cached_tokens"), "provider_reported_reasoning_tokens": completion_details.get("reasoning_tokens"), "provider_reported_thinking_tokens": None, "usage_source": "provider_reported" if available else "unavailable", "load_duration_ms": None, "prompt_eval_duration_ms": seconds_ms(raw.get("prompt_time")), "generation_duration_ms": seconds_ms(raw.get("completion_time")), "provider_queue_duration_ms": seconds_ms(raw.get("queue_time")), "provider_total_duration_ms": seconds_ms(raw.get("total_time")), "native_usage": raw}
    return {"provider_reported_input_tokens": None, "provider_reported_output_tokens": None, "provider_reported_total_tokens": None, "provider_reported_cached_input_tokens": None, "provider_reported_reasoning_tokens": None, "provider_reported_thinking_tokens": None, "usage_source": "unavailable", "load_duration_ms": None, "prompt_eval_duration_ms": None, "generation_duration_ms": None, "native_usage": raw}


def _ns_ms(value):
    return None if value is None else value / 1_000_000


def calculate_token_cost(input_tokens, output_tokens, *, input_price, output_price, cached_tokens=0, cached_input_price=None):
    if input_tokens is None or output_tokens is None:
        return None
    cached = min(cached_tokens or 0, input_tokens)
    uncached = input_tokens - cached
    cached_rate = input_price if cached_input_price is None else cached_input_price
    return (uncached * input_price + cached * cached_rate + output_tokens * output_price) / 1_000_000


def active_scenario_pricing(connection) -> dict | None:
    row = connection.execute("SELECT * FROM scenario_pricing ORDER BY effective_date DESC, revision DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def persist_usage(*, execution_id: str, run_id: str, project_id: str, operation: str, provider: str, model: str, normalized: dict, duration_ms: int, composition: dict, supplied_source: list[dict], cited_source: list[dict], wiki: list[dict], source_hash: str, wiki_hash: str, status: str, warnings: list[dict] | None = None, run_purpose: str = "development", model_configuration: dict | None = None) -> dict:
    api_cost, pricing_revision = (0.0, "ollama-local-v1") if provider == "ollama" else (None, None)
    if provider == "openrouter" and normalized.get("provider_reported_cost") is not None:
        api_cost, pricing_revision = normalized["provider_reported_cost"], "openrouter-provider-reported"
    with db() as connection:
        if model_configuration is None:
            settings = get_settings()
            runtime = connection.execute("SELECT digest,metadata_json FROM model_runtime_metadata WHERE provider=? AND model=?", (provider, model)).fetchone()
            native = json.loads(runtime["metadata_json"]) if runtime else {}
            model_configuration = {"provider":provider,"exact_model_tag":model,"digest":runtime["digest"] if runtime else None,"quantization":native.get("quantization"),"reasoning":("enabled" if settings.ollama_think_enabled else "disabled") if provider=="ollama" else None,"num_ctx":settings.ollama_context_length if provider=="ollama" else None,"num_predict":settings.ollama_num_predict if provider=="ollama" else None,"temperature":0.1,"timeout_seconds":getattr(settings,f"{provider}_timeout_seconds",None),"serialization_version":settings.prompt_serialization_version}
            if provider == "groq":
                model_configuration.update({"reasoning_effort":settings.groq_reasoning_effort or "provider_default","reasoning_format":settings.groq_reasoning_format,"include_reasoning":settings.groq_include_reasoning,"temperature":"provider_default","max_output_tokens":settings.groq_max_output_tokens,"base_url":settings.groq_base_url})
            if provider == "openrouter":
                model_configuration.update({"deployment_route":f"{settings.openrouter_model} accessed through OpenRouter","max_output_tokens":settings.openrouter_max_output_tokens,"base_url":settings.openrouter_base_url})
                if model.lower() in {"openai/gpt-5.1", "gpt-5.1"}:
                    model_configuration["presentation_prompt_version"] = settings.gpt51_presentation_version
        scenario = active_scenario_pricing(connection)
        actual_pricing = connection.execute("SELECT * FROM model_pricing WHERE provider=? AND model=? ORDER BY effective_from DESC,created_at DESC LIMIT 1", (provider, model)).fetchone()
        if actual_pricing and api_cost is None:
            api_cost = calculate_token_cost(normalized.get("provider_reported_input_tokens"), normalized.get("provider_reported_output_tokens"), input_price=actual_pricing["input_price_per_million"], cached_input_price=actual_pricing["cached_input_price_per_million"], output_price=actual_pricing["output_price_per_million"], cached_tokens=normalized.get("provider_reported_cached_input_tokens"))
            pricing_revision = actual_pricing["pricing_revision"]
        if provider == "openai" and scenario and model == scenario["model"]:
            api_cost = calculate_token_cost(normalized.get("provider_reported_input_tokens"), normalized.get("provider_reported_output_tokens"), input_price=scenario["uncached_input_price_per_million"], cached_input_price=scenario["cached_input_price_per_million"], output_price=scenario["output_price_per_million"], cached_tokens=normalized.get("provider_reported_cached_input_tokens"))
            pricing_revision = scenario["revision"]
        connection.execute("""INSERT OR REPLACE INTO model_usage (execution_id,run_id,project_id,operation,provider,model,provider_reported_input_tokens,provider_reported_output_tokens,provider_reported_total_tokens,provider_reported_cached_input_tokens,provider_reported_reasoning_tokens,provider_reported_thinking_tokens,usage_source,request_duration_ms,load_duration_ms,prompt_eval_duration_ms,generation_duration_ms,api_cost,compute_energy_cost,pricing_revision,native_usage_json,prompt_composition_json,supplied_source_chunk_ids_json,cited_source_chunk_ids_json,supplied_wiki_chunk_ids_json,supplied_source_package_hash,supplied_wiki_package_hash,status,warnings_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            execution_id,run_id,project_id,operation,provider,model,normalized.get("provider_reported_input_tokens"),normalized.get("provider_reported_output_tokens"),normalized.get("provider_reported_total_tokens"),normalized.get("provider_reported_cached_input_tokens"),normalized.get("provider_reported_reasoning_tokens"),normalized.get("provider_reported_thinking_tokens"),normalized.get("usage_source","unavailable"),duration_ms,normalized.get("load_duration_ms"),normalized.get("prompt_eval_duration_ms"),normalized.get("generation_duration_ms"),api_cost,None,pricing_revision,json.dumps(normalized.get("native_usage",{})),json.dumps(composition),json.dumps([x["chunk_id"] for x in supplied_source]),json.dumps([x["chunk_id"] for x in cited_source]),json.dumps([x.get("chunk_id") for x in wiki]),source_hash,wiki_hash,status,json.dumps(warnings or []),datetime.now(timezone.utc).isoformat()))
        connection.execute("UPDATE model_usage SET run_purpose=?, model_configuration_json=? WHERE execution_id=?", (run_purpose, json.dumps(model_configuration or {}), execution_id))
        connection.execute("UPDATE model_usage SET provider_queue_duration_ms=?,provider_total_duration_ms=? WHERE execution_id=?", (normalized.get("provider_queue_duration_ms"), normalized.get("provider_total_duration_ms"), execution_id))
    return {**normalized, "execution_id": execution_id, "request_duration_ms": duration_ms, "api_cost": api_cost, "compute_energy_cost": None, "prompt_composition": composition}


def usage_summary(project_id: str | None = None) -> dict:
    where, params = (" WHERE project_id = ?", (project_id,)) if project_id else ("", ())
    with db() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM model_usage" + where + " ORDER BY created_at DESC", params).fetchall()]
        pricing = active_scenario_pricing(connection)
    for row in rows:
        row["prompt_composition"] = json.loads(row.pop("prompt_composition_json") or "{}")
        row["supplied_source_chunk_ids"] = json.loads(row.pop("supplied_source_chunk_ids_json") or "[]")
        row["cited_source_chunk_ids"] = json.loads(row.pop("cited_source_chunk_ids_json") or "[]")
        row["supplied_wiki_chunk_ids"] = json.loads(row.pop("supplied_wiki_chunk_ids_json") or "[]")
        row["safe_native_usage"] = json.loads(row.pop("native_usage_json") or "{}")
        if row.get("provider") == "openrouter" and row.get("api_cost") is None:
            reported_cost = row["safe_native_usage"].get("cost")
            try:
                row["api_cost"] = float(reported_cost) if reported_cost is not None else None
            except (TypeError, ValueError):
                row["api_cost"] = None
            if row["api_cost"] is not None:
                row["pricing_revision"] = row.get("pricing_revision") or "openrouter-provider-reported"
        row["warnings"] = json.loads(row.pop("warnings_json") or "[]")
        row["model_configuration"] = json.loads(row.pop("model_configuration_json") or "{}")
        row["gpt_equivalent_estimate"] = calculate_token_cost(row.get("provider_reported_input_tokens"), row.get("provider_reported_output_tokens"), input_price=pricing["uncached_input_price_per_million"], output_price=pricing["output_price_per_million"]) if pricing else None
    def aggregate(key):
        groups = {}
        for row in rows:
            name = row[key]
            group = groups.setdefault(name, {key: name, "provider": row.get("provider"), "calls": 0, "input_tokens": None, "output_tokens": None, "total_tokens": None, "reasoning_tokens": None, "latency_ms": 0, "actual_provider_api_cost": None, "gpt_equivalent_estimate": None})
            group["calls"] += 1
            for field, target in (("provider_reported_input_tokens","input_tokens"),("provider_reported_output_tokens","output_tokens"),("provider_reported_total_tokens","total_tokens"),("request_duration_ms","latency_ms")):
                if row.get(field) is not None:
                    group[target] = (group[target] or 0) + row[field]
            if row.get("provider_reported_reasoning_tokens") is not None:
                group["reasoning_tokens"] = (group["reasoning_tokens"] or 0) + row["provider_reported_reasoning_tokens"]
            for field in ("api_cost", "gpt_equivalent_estimate"):
                if row.get(field) is not None:
                    target = "actual_provider_api_cost" if field == "api_cost" else field
                    group[target] = (group[target] or 0) + row[field]
        return list(groups.values())
    total_or_none = lambda field: sum(r[field] for r in rows if r.get(field) is not None) if any(r.get(field) is not None for r in rows) else None
    equivalent_total = sum(r["gpt_equivalent_estimate"] for r in rows if r.get("gpt_equivalent_estimate") is not None) if any(r.get("gpt_equivalent_estimate") is not None for r in rows) else None
    actual_total = sum(r["api_cost"] for r in rows if r.get("api_cost") is not None) if any(r.get("api_cost") is not None for r in rows) else None
    return {"overview": {"requests": len(rows), "actual_input_tokens": total_or_none("provider_reported_input_tokens"), "actual_output_tokens": total_or_none("provider_reported_output_tokens"), "actual_total_tokens": total_or_none("provider_reported_total_tokens"), "cached_tokens": total_or_none("provider_reported_cached_input_tokens"), "actual_provider_api_cost": actual_total, "gpt_equivalent_estimate": equivalent_total, "local_model_generation_time_ms": total_or_none("generation_duration_ms"), "local_hardware_energy_cost": None}, "scenario_pricing": pricing, "by_model": aggregate("model"), "by_operation": aggregate("operation"), "recent_executions": rows[:50]}
