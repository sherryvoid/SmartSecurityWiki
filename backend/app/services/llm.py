import httpx

from app.core.config import Settings, get_settings


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
        except httpx.ReadTimeout:
            return provider_error_response("Ollama", f"Timed out after {self.settings.ollama_timeout_seconds:g}s. The local model may still be loading or generating slowly.")
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
            async with httpx.AsyncClient(timeout=self.settings.cloud_llm_timeout_seconds) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return {"content": data["choices"][0]["message"]["content"], "raw": data, "ok": True}
        except httpx.ReadTimeout:
            return provider_error_response("OpenAI", f"Timed out after {self.settings.cloud_llm_timeout_seconds:g}s.")
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
            async with httpx.AsyncClient(timeout=self.settings.cloud_llm_timeout_seconds) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            return {"content": content, "raw": data, "ok": True}
        except httpx.ReadTimeout:
            return provider_error_response("Gemini", f"Timed out after {self.settings.cloud_llm_timeout_seconds:g}s.")
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
            async with httpx.AsyncClient(timeout=self.settings.cloud_llm_timeout_seconds) as client:
                response = await client.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            return {"content": data["choices"][0]["message"]["content"], "raw": data, "ok": True}
        except httpx.ReadTimeout:
            return provider_error_response("DeepSeek", f"Timed out after {self.settings.cloud_llm_timeout_seconds:g}s.")
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


def provider_for(name: str, settings: Settings | None = None) -> tuple[LLMProvider, str]:
    settings = settings or get_settings()
    providers: dict[str, tuple[LLMProvider, str]] = {
        "ollama": (OllamaProvider(settings), settings.ollama_default_model),
        "openai": (OpenAIProvider(settings), settings.openai_default_model),
        "gemini": (GeminiProvider(settings), settings.gemini_default_model),
        "deepseek": (DeepSeekProvider(settings), settings.deepseek_default_model),
    }
    return providers.get(name, providers["ollama"])
