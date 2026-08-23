# READ SUMMARY: This module wraps LLM providers and structured Security Wiki response validation.
# CHANGED: Added provider availability checks plus per-provider timeout handling that returns structured graceful timeout responses.
import json
import re

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.db.schemas import SecurityWikiSchema


SECURITY_WIKI_JSON_INSTRUCTION = (
    "You MUST respond with ONLY valid JSON matching the provided schema. "
    "Do not include markdown fences, explanations, or any text outside the JSON object."
)


class LLMProvider:
    name = "base"

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1, **kwargs) -> dict:
        raise NotImplementedError

SIMPLE_PROMPT_TEMPLATE = """You are a security code auditor.
Answer using ONLY the source code evidence provided below.
If the evidence does not contain the answer, write exactly:
The evidence does not show this.
Do NOT invent file paths, line numbers, class names, or role names.

SOURCE CODE EVIDENCE:
{evidence_text}

QUESTION: {question}

Write your answer clearly and completely.
At the end of your answer, on separate lines, write:
FILE: <the most relevant file_path from the evidence, or unknown>
LINE: <the most relevant start_line number from the evidence, or unknown>
CONFIDENCE: high
LIMITATIONS: <what information was missing from the evidence>
"""

def _strip_think_tags(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(authorization|api[-_ ]?key|token|password|cookie)\s*[:=]\s*([^\s,;]+)"
)


def sanitize_diagnostic_text(value: object, max_chars: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = SENSITIVE_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}: [REDACTED]", text)
    if len(text) > max_chars:
        return text[:max_chars] + "… [truncated]"
    return text


def is_provider_available(provider: str) -> bool:
    """
    Return True only when the provider can actually be called.

    Cloud providers need a non-empty API key in environment. Ollama needs the
    local server to respond within 3 seconds.
    """
    provider = provider.lower().strip()
    settings = get_settings()
    if provider == "gemini":
        return bool(settings.gemini_api_key.strip())
    if provider == "openai":
        return bool(settings.openai_api_key.strip())
    if provider == "groq":
        return bool(settings.groq_api_key.strip())
    if provider == "ollama":
        try:
            url = settings.ollama_base_url.rstrip("/")
            response = httpx.get(f"{url}/api/tags", timeout=3.0)
            return response.status_code < 500
        except Exception:
            return False
    return False


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1, **kwargs) -> dict:
        source_chunks = kwargs.get("source_chunks")
        question = kwargs.get("question")
        
        if source_chunks is not None and question is not None:
            evidence_lines = []
            for i, chunk in enumerate(source_chunks, 1):
                evidence_lines.append(
                    f"[{i}] File: {chunk.get('file_path','?')}, "
                    f"Lines {chunk.get('start_line','?')}-{chunk.get('end_line','?')}, "
                    f"Symbol: {chunk.get('symbol','?')}\n"
                    f"{chunk.get('code','')}"
                )
            evidence_text = "\n\n".join(evidence_lines)
            prompt = SIMPLE_PROMPT_TEMPLATE.format(
                evidence_text=evidence_text,
                question=question
            )
            import logging
            logger = logging.getLogger(__name__)
            logger.info("[LLM] Ollama using SIMPLE_PROMPT_TEMPLATE, evidence_count=%d", len(source_chunks))
            messages = [{"role": "user", "content": prompt}]
            
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": self.settings.ollama_think_enabled,
            "options": {"temperature": temperature, "num_predict": self.settings.ollama_num_predict, "num_ctx": self.settings.ollama_context_length},
        }
        think_option_fallback = False
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(f"{self.settings.ollama_base_url}/api/chat", json=payload)
                if response.status_code == 400 and "think" in response.text.lower():
                    think_option_fallback = True
                    compatibility_payload = dict(payload)
                    compatibility_payload.pop("think", None)
                    response = await client.post(f"{self.settings.ollama_base_url}/api/chat", json=compatibility_payload)
                response.raise_for_status()
                data = response.json()
                
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            provider_text = message.get("content", "") or ""
            thinking = message.get("thinking", "") or ""
            thinking_present = bool(thinking)
            raw_text = _strip_think_tags(provider_text)
            think_tags_removed = raw_text != provider_text.strip()
            
            answer_text = raw_text
            file_val = "unknown"
            line_val = "unknown"
            confidence_val = "medium"
            limitations_val = "Local model did not provide structured output."

            for line in raw_text.splitlines():
                line_stripped = line.strip()
                if line_stripped.startswith("FILE:"):
                    file_val = line_stripped[5:].strip()
                elif line_stripped.startswith("LINE:"):
                    line_val = line_stripped[5:].strip()
                elif line_stripped.startswith("CONFIDENCE:"):
                    confidence_val = line_stripped[11:].strip().lower()
                elif line_stripped.startswith("LIMITATIONS:"):
                    limitations_val = line_stripped[12:].strip()

            answer_lines = []
            skip_keys = ("FILE:", "LINE:", "CONFIDENCE:", "LIMITATIONS:")
            for line in raw_text.splitlines():
                if not any(line.strip().startswith(k) for k in skip_keys):
                    answer_lines.append(line)
            answer_text = "\n".join(answer_lines).strip()

            validation_status = "completed_with_warnings" if answer_text else "empty_response"
            empty_classification = None if answer_text else ("no_content_with_thinking" if thinking_present else "fully_empty_response")
            if not answer_text:
                answer_text = (
                    "The local model returned reasoning metadata but no final answer. Thinking content was not retained. "
                    "Disable thinking or increase the configured output capacity and retry."
                    if thinking_present else
                    "The local model returned no final answer content. Increase the configured output capacity or retry."
                )
            
            import json
            parsed_answer_json = json.dumps({
                "answer": answer_text,
                "file": file_val,
                "line": line_val,
                "confidence": confidence_val,
                "limitations": limitations_val,
                "validation_status": validation_status
            })
            
            return {
                "content": answer_text, 
                "parsed_answer_json": parsed_answer_json,
                "validation_status": validation_status,
                "diagnostics": {
                    "status": validation_status,
                    "raw_response": sanitize_diagnostic_text(provider_text, self.settings.diagnostic_raw_response_max_chars),
                    "envelope": {
                        "thinking_present": thinking_present,
                        "thinking_length": len(thinking),
                        "content_present": bool(provider_text),
                        "content_length": len(provider_text),
                        "done": data.get("done"),
                        "done_reason": data.get("done_reason"),
                        "prompt_eval_count": data.get("prompt_eval_count"),
                        "eval_count": data.get("eval_count"),
                        "total_duration": data.get("total_duration"),
                        "load_duration": data.get("load_duration"),
                        "think_enabled": self.settings.ollama_think_enabled,
                        "think_option_fallback": think_option_fallback,
                        "num_predict": self.settings.ollama_num_predict,
                        "context_length": self.settings.ollama_context_length,
                    },
                    "processing": {
                        "response_received": True,
                        "raw_response_length": len(provider_text),
                        "think_tags_removed": think_tags_removed,
                        "parser": "ollama_plain_text",
                        "parse_status": "accepted_plain_text" if validation_status != "empty_response" else empty_classification,
                        "schema_validation_status": "not_required",
                        "evidence_validation_status": "model_references_unavailable",
                    },
                },
                "raw": {"message": {"content": provider_text}, **{key: data.get(key) for key in ("done", "done_reason", "prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration")}},
                "usage": {key: data.get(key) for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration") if data.get(key) is not None},
                "ok": validation_status != "empty_response"
            }
        except httpx.TimeoutException:
            return provider_timeout_response("Ollama", self.settings.ollama_timeout_seconds)
        except httpx.ConnectError:
            return provider_error_response("Ollama", "Could not connect to Ollama. Make sure Ollama is running.")
        except httpx.HTTPStatusError as exc:
            return provider_error_response("Ollama", f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:300]}")
        except httpx.HTTPError as exc:
            return provider_error_response("Ollama", str(exc))


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1, **kwargs) -> dict:
        if not self.settings.openai_api_key:
            return missing_key_response("OpenAI")
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            async with httpx.AsyncClient(timeout=self.settings.openai_timeout_seconds) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            return {"content": content, "raw": data, "usage": data.get("usage") or {}, "ok": True, "diagnostics": _cloud_processing_diagnostics(content)}
        except httpx.TimeoutException:
            return provider_timeout_response("OpenAI", self.settings.openai_timeout_seconds)
        except httpx.HTTPError as exc:
            return provider_error_response("OpenAI", str(exc))


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1, **kwargs) -> dict:
        if not self.settings.gemini_api_key:
            return missing_key_response("Gemini")
        text = "\n\n".join(f"{message['role'].upper()}:\n{message['content']}" for message in messages)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.settings.gemini_api_key}"
        payload = {"contents": [{"parts": [{"text": text}]}], "generationConfig": {"temperature": temperature}}
        try:
            async with httpx.AsyncClient(timeout=self.settings.gemini_timeout_seconds) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return {"content": content, "raw": data, "usage": data.get("usageMetadata") or {}, "ok": True, "diagnostics": _cloud_processing_diagnostics(content)}
        except httpx.TimeoutException:
            return provider_timeout_response("Gemini", self.settings.gemini_timeout_seconds)
        except httpx.HTTPError as exc:
            return provider_error_response("Gemini", str(exc))


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1, **kwargs) -> dict:
        if not self.settings.groq_api_key:
            return missing_key_response("Groq")
        payload = {
            "model": model,
            "messages": messages,
            "reasoning_effort": self.settings.groq_reasoning_effort,
            "include_reasoning": self.settings.groq_include_reasoning,
            "max_completion_tokens": self.settings.groq_max_output_tokens,
        }
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.groq_timeout_seconds) as client:
                response = await client.post(f"{self.settings.groq_base_url.rstrip('/')}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            if not isinstance(content, str) or not content.strip():
                return groq_error_response("malformed", "Groq returned a response SecurityCodeWiki could not process.")
            return {
                "content": content,
                "raw": {"id": data.get("id"), "model": data.get("model"), "system_fingerprint": data.get("system_fingerprint")},
                "usage": data.get("usage") or {},
                "ok": True,
                "diagnostics": {**_cloud_processing_diagnostics(content), "envelope": {"reasoning_effort": self.settings.groq_reasoning_effort, "reasoning_format": self.settings.groq_reasoning_format, "include_reasoning": self.settings.groq_include_reasoning, "max_completion_tokens": self.settings.groq_max_output_tokens}},
            }
        except httpx.TimeoutException:
            result = provider_timeout_response("Groq", self.settings.groq_timeout_seconds)
            result["content"] = "Groq did not complete within the configured timeout."
            return result
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                return groq_error_response("authentication", "Groq authentication failed. Check GROQ_API_KEY.", status)
            if status == 429:
                return groq_error_response("rate_limit", "Groq rate limit reached. Try again after the provider retry window.", status)
            if status in {400, 404} and "model" in exc.response.text.lower():
                return groq_error_response("model_unavailable", "The selected Groq model is currently unavailable.", status)
            return groq_error_response("provider", "Groq could not complete the request.", status)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return groq_error_response("malformed", "Groq returned a response SecurityCodeWiki could not process.")
        except httpx.HTTPError:
            return groq_error_response("provider", "Groq could not complete the request.")


def _cloud_processing_diagnostics(content: str) -> dict:
    return {"processing": {"response_received": True, "content_present": bool(content), "content_length": len(content), "parser": "provider_text", "parse_status": "received", "schema_validation_status": "pending", "evidence_validation_status": "pending"}}


def missing_key_response(provider: str) -> dict:
    return {
        "content": f"{provider} API key is not configured. Not verified from the available evidence.",
        "raw": {"error": "missing_api_key"},
        "ok": False,
    }


def provider_error_response(provider: str, detail: str) -> dict:
    return {
        "content": f"{provider} provider error: {detail}\n\nNot verified from the available source-code evidence.",
        "raw": {"error": "provider_error", "detail": detail},
        "ok": False,
    }


def provider_timeout_response(provider: str, timeout_seconds: float) -> dict:
    return {
        "content": "Model did not respond within the time limit.",
        "answer": "Model did not respond within the time limit.",
        "confidence": "none",
        "validation_status": "timeout",
        "display_status": "Model timed out",
        "evidence_refs": [],
        "limitations": "Local model timed out. Use a cloud model for comparison.",
        "raw": {"error": "timeout", "provider": provider, "timeout_seconds": timeout_seconds},
        "ok": False,
    }


def groq_error_response(category: str, message: str, status_code: int | None = None) -> dict:
    return {"content": message, "raw": {"error": "provider_error", "category": category, "status_code": status_code}, "error": {"error_code": f"GROQ_{category.upper()}", "user_message": message, "technical_message": f"Groq HTTP status {status_code}" if status_code else category, "retryable": category in {"rate_limit", "provider"}, "provider": "groq"}, "validation_status": "provider_unavailable", "ok": False}


def provider_for(name: str, settings: Settings | None = None) -> tuple[LLMProvider, str]:
    settings = settings or get_settings()
    providers: dict[str, tuple[LLMProvider, str]] = {
        "ollama": (OllamaProvider(settings), settings.ollama_default_model),
        "openai": (OpenAIProvider(settings), settings.openai_default_model),
        "gemini": (GeminiProvider(settings), settings.resolved_gemini_default_model),
        "groq": (GroqProvider(settings), settings.groq_default_model),
    }
    if name not in providers:
        raise ValueError(f"Provider '{name}' is not active.")
    return providers[name]


def active_provider_names() -> tuple[str, ...]:
    return ("ollama", "openai", "gemini", "groq")


def security_wiki_system_prompt() -> str:
    schema = json.dumps(SecurityWikiSchema.model_json_schema(), indent=2)
    return (
        "You are generating a structured Security Wiki from source-code evidence only.\n"
        f"{SECURITY_WIKI_JSON_INSTRUCTION}\n\n"
        "SecurityWikiSchema JSON schema:\n"
        f"{schema}"
    )


def strip_markdown_fences(response_text: str) -> str:
    stripped = response_text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_security_wiki_response(response_text: str) -> SecurityWikiSchema:
    return SecurityWikiSchema.model_validate_json(strip_markdown_fences(response_text))


async def generate_structured_security_wiki(provider: LLMProvider, messages: list[dict], model: str) -> tuple[SecurityWikiSchema, str, str]:
    wiki, response_text, status, _ = await generate_structured_security_wiki_diagnostic(provider, messages, model)
    legacy_status = {"wiki_completed": "valid_json", "wiki_completed_with_warnings": "valid_json_repaired"}.get(status, "invalid_json_fallback")
    return wiki, response_text, legacy_status


async def generate_structured_security_wiki_diagnostic(provider: LLMProvider, messages: list[dict], model: str) -> tuple[SecurityWikiSchema, str, str, dict]:
    result = await provider.generate(messages, model)
    response_text = result.get("content") or ""
    diagnostics = {**result.get("diagnostics", {}), "usage": result.get("usage", {})}
    provider_status = result.get("validation_status")
    if provider_status in {"timeout", "provider_timeout"}:
        return _failed_wiki(response_text, "Provider timed out before producing a Wiki."), response_text, "wiki_provider_timeout", diagnostics
    if not response_text or provider_status == "empty_response":
        return _failed_wiki("", "Provider returned no final Wiki content."), "", "wiki_empty_response", diagnostics
    if result.get("ok") is False:
        return _failed_wiki(response_text, "Provider was unavailable or returned an error."), response_text, "wiki_provider_unavailable", diagnostics
    try:
        return parse_security_wiki_response(response_text), response_text, "wiki_completed", diagnostics
    except ValidationError:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": response_text},
            {"role": "user", "content": "The previous response was not valid JSON. Return ONLY the JSON object, nothing else."},
        ]
        repair_result = await provider.generate(repair_messages, model)
        repair_text = repair_result.get("content") or ""
        try:
            return parse_security_wiki_response(repair_text), repair_text, "wiki_completed_with_warnings", {**repair_result.get("diagnostics", diagnostics), "usage": repair_result.get("usage", {})}
        except ValidationError:
            try:
                json.loads(strip_markdown_fences(repair_text or response_text))
                failure_status = "wiki_schema_validation_failed"
                limitation = "Model output was JSON but did not satisfy the Security Wiki schema."
            except json.JSONDecodeError:
                failure_status = "wiki_parse_failed"
                limitation = "Failed Wiki draft — unstructured model output."
            return _failed_wiki(response_text, limitation), response_text, failure_status, repair_result.get("diagnostics", diagnostics)


def _failed_wiki(raw_text: str, limitation: str) -> SecurityWikiSchema:
    return SecurityWikiSchema.model_validate({
        "module_overview": raw_text,
        "entry_points": [],
        "access_control_matrix": [],
        "vertical_helpers": [],
        "requirement_traces": [],
        "limitations": limitation,
        "parse_failed": True,
    })
