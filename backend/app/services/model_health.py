import httpx

from app.core.config import Settings, get_settings
from app.services.vector_index import embedding_status


async def models_health(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    ollama = await _ollama_health(settings)
    return {
        "ollama": ollama,
        "openai": _cloud_health(settings.openai_api_key, settings.openai_default_model),
        "gemini": _cloud_health(settings.gemini_api_key, settings.resolved_gemini_default_model),
        "deepseek": _cloud_health(settings.deepseek_api_key, settings.deepseek_default_model),
        "embedding": embedding_status(),
    }


async def _ollama_health(settings: Settings) -> dict:
    result = {
        "base_url": settings.ollama_base_url,
        "reachable": False,
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
        "status": status,
    }
