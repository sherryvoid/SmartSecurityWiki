# READ SUMMARY: This module wraps LLM providers and structured Security Wiki response validation.
# CHANGED: Added per-provider timeout handling that returns structured graceful timeout responses.
import json

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

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1) -> dict:
        raise NotImplementedError


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1) -> dict:
        payload = {"model": model, "messages": messages, "stream": False, "options": {"temperature": temperature}}
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds) as client:
                response = await client.post(f"{self.settings.ollama_base_url}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
            return {"content": data.get("message", {}).get("content", ""), "raw": data, "ok": True}
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

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1) -> dict:
        if not self.settings.openai_api_key:
            return missing_key_response("OpenAI")
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            async with httpx.AsyncClient(timeout=self.settings.openai_timeout_seconds) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return {"content": data["choices"][0]["message"]["content"], "raw": data, "ok": True}
        except httpx.TimeoutException:
            return provider_timeout_response("OpenAI", self.settings.openai_timeout_seconds)
        except httpx.HTTPError as exc:
            return provider_error_response("OpenAI", str(exc))


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1) -> dict:
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
            return {"content": content, "raw": data, "ok": True}
        except httpx.TimeoutException:
            return provider_timeout_response("Gemini", self.settings.gemini_timeout_seconds)
        except httpx.HTTPError as exc:
            return provider_error_response("Gemini", str(exc))


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, messages: list[dict], model: str, temperature: float = 0.1) -> dict:
        if not self.settings.deepseek_api_key:
            return missing_key_response("DeepSeek")
        headers = {"Authorization": f"Bearer {self.settings.deepseek_api_key}"}
        payload = {"model": model, "messages": messages, "temperature": temperature}
        try:
            async with httpx.AsyncClient(timeout=self.settings.deepseek_timeout_seconds) as client:
                response = await client.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return {"content": data["choices"][0]["message"]["content"], "raw": data, "ok": True}
        except httpx.TimeoutException:
            return provider_timeout_response("DeepSeek", self.settings.deepseek_timeout_seconds)
        except httpx.HTTPError as exc:
            return provider_error_response("DeepSeek", str(exc))


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


def provider_for(name: str, settings: Settings | None = None) -> tuple[LLMProvider, str]:
    settings = settings or get_settings()
    providers: dict[str, tuple[LLMProvider, str]] = {
        "ollama": (OllamaProvider(settings), settings.ollama_default_model),
        "openai": (OpenAIProvider(settings), settings.openai_default_model),
        "gemini": (GeminiProvider(settings), settings.resolved_gemini_default_model),
        "deepseek": (DeepSeekProvider(settings), settings.deepseek_default_model),
    }
    return providers.get(name, providers["ollama"])


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
    result = await provider.generate(messages, model)
    response_text = result.get("content") or ""
    try:
        return parse_security_wiki_response(response_text), response_text, "valid_json"
    except ValidationError:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": response_text},
            {"role": "user", "content": "The previous response was not valid JSON. Return ONLY the JSON object, nothing else."},
        ]
        repair_result = await provider.generate(repair_messages, model)
        repair_text = repair_result.get("content") or ""
        try:
            return parse_security_wiki_response(repair_text), repair_text, "valid_json_repaired"
        except ValidationError:
            fallback = SecurityWikiSchema.model_validate(
                {
                    "module_overview": response_text,
                    "entry_points": [],
                    "access_control_matrix": [],
                    "vertical_helpers": [],
                    "requirement_traces": [],
                    "limitations": "Wiki could not be structured \u2014 stored as raw text.",
                    "parse_failed": True,
                }
            )
            return fallback, response_text, "invalid_json_fallback"
