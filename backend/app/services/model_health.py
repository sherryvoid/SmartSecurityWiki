# READ SUMMARY: This module reports whether configured LLM and embedding providers are ready for use.
# CHANGED: Added available/reason provider health while preserving existing frontend status fields.
import httpx

from app.core.config import Settings, get_settings
from app.services.llm import is_provider_available
from app.services.vector_index import embedding_status


async def models_health(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    ollama = await _ollama_health(settings)
    groq = await _groq_health(settings)
    return {
        "ollama": ollama,
        "openai": _cloud_health(settings.openai_api_key, settings.openai_default_model),
        "gemini": _cloud_health(settings.gemini_api_key, settings.resolved_gemini_default_model),
        "groq": groq,
        "embedding": embedding_status(),
    }


async def _ollama_health(settings: Settings) -> dict:
    available = is_provider_available("ollama")
    result = {
        "base_url": settings.ollama_base_url,
        "reachable": False,
        "available": available,
        "reason": "Local server responding" if available else "Ollama local server not responding",
        "available_models": [],
        "default_model": settings.ollama_default_model,
        "default_model_exists": False,
        "status": "Ollama not running",
        "detail": None,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        result["detail"] = str(exc)
        return result

    models = [item.get("name", "") for item in data.get("models", []) if item.get("name")]
    result["reachable"] = True
    result["available_models"] = models
    result["default_model_exists"] = settings.ollama_default_model in models
    result["status"] = "Ready" if result["default_model_exists"] else "Model not found"
    return result


def _cloud_health(api_key: str, default_model: str) -> dict:
    api_key_configured = bool(api_key.strip())
    default_model_configured = bool(default_model.strip())
    if api_key_configured and default_model_configured:
        status = "Ready"
    elif not api_key_configured:
        status = "Not configured"
    else:
        status = "Default model missing"
    return {
        "api_key_configured": api_key_configured,
        "default_model_configured": default_model_configured,
        "default_model": default_model,
        "available": api_key_configured,
        "reason": "API key configured" if api_key_configured else _missing_key_reason(default_model),
        "status": status,
    }


async def _groq_health(settings: Settings) -> dict:
    base = {"api_key_configured": bool(settings.groq_api_key.strip()), "default_model_configured": bool(settings.groq_default_model.strip()), "default_model": settings.groq_default_model, "available_models": [], "available": False, "reachable": False, "status": "Not configured", "reason": "GROQ_API_KEY not configured"}
    if not settings.groq_api_key.strip():
        return base
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.groq_base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {settings.groq_api_key}"})
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        reason = "Groq authentication failed. Check GROQ_API_KEY." if status == 401 else "Groq rate limit reached." if status == 429 else "Groq provider error."
        return {**base, "status": "Authentication failed" if status == 401 else "Rate limited" if status == 429 else "Provider error", "reason": reason}
    except httpx.HTTPError:
        return {**base, "status": "Provider error", "reason": "Groq provider is not reachable."}
    discovered = {item.get("id") for item in data.get("data", []) if isinstance(item, dict) and item.get("id")}
    active = [model for model in settings.groq_active_model_ids if model in discovered]
    default_exists = settings.groq_default_model in active
    return {**base, "reachable": True, "available_models": active, "available": default_exists, "default_model_exists": default_exists, "status": "Ready" if default_exists else "Model unavailable", "reason": "Groq provider reachable" if default_exists else "The selected Groq model is currently unavailable."}


def _missing_key_reason(default_model: str) -> str:
    lowered = default_model.lower()
    if "gemini" in lowered:
        return "GEMINI_API_KEY not set"
    return "OPENAI_API_KEY not set"
