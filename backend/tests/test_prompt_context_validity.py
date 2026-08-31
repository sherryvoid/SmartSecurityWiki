import asyncio


class LocalProvider:
    name = "ollama"

    def __init__(self, settings, exact_tokens=None):
        self.settings = settings
        self.exact_tokens = exact_tokens
        self.calls = 0

    def count_prompt_tokens(self, messages, model):
        if self.exact_tokens is None:
            raise RuntimeError("exact tokenizer unavailable")
        return self.exact_tokens

    async def generate(self, messages, model):
        self.calls += 1
        return {"content": "ok", "ok": True, "diagnostics": {}}


def _settings(context=100, output=20):
    from app.core.config import Settings
    return Settings(ollama_context_length=context, ollama_num_predict=output)


def test_prompt_fit_allows_provider_call():
    from app.services.audit_service import safe_generate
    provider = LocalProvider(_settings(), exact_tokens=70)
    result = asyncio.run(safe_generate(provider, [{"role": "user", "content": "neutral policy"}], "neutral-model"))
    assert provider.calls == 1
    assert result["diagnostics"]["prompt_fit"]["passed"] is True
    assert result["diagnostics"]["prompt_fit"]["required_total_tokens"] == 90


def test_oversized_prompt_is_rejected_before_provider_call():
    from app.services.audit_service import safe_generate
    provider = LocalProvider(_settings(), exact_tokens=81)
    result = asyncio.run(safe_generate(provider, [{"role": "user", "content": "neutral policy"}], "neutral-model"))
    assert provider.calls == 0
    assert result["validation_status"] == "provider_context_incompatible"
    assert result["diagnostics"]["prompt_fit"] == {
        "applicable": True,
        "passed": False,
        "provider": "ollama",
        "model": "neutral-model",
        "configured_context": 100,
        "prompt_tokens": 81,
        "count_type": "exact",
        "reserved_output_tokens": 20,
        "required_total_tokens": 101,
        "estimate_safety_factor": None,
    }


def test_output_reservation_can_reject_otherwise_fitting_prompt():
    from app.services.llm import prompt_fit_preflight
    provider = LocalProvider(_settings(context=100, output=30), exact_tokens=75)
    result = prompt_fit_preflight(provider, [{"role": "user", "content": "neutral policy"}], "neutral-model")
    assert result["prompt_tokens"] < result["configured_context"]
    assert result["required_total_tokens"] == 105
    assert result["passed"] is False


def test_default_frozen_local_configuration():
    from app.core.config import Settings
    settings = Settings(_env_file=None)
    assert settings.ollama_default_model == "qwen3.5:9b"
    assert settings.ollama_context_length == 20480
    assert settings.ollama_num_predict == 2048
    assert settings.ollama_think_enabled is False


def test_qwen_and_nemotron_frozen_q1_counts_fit():
    from app.services.llm import prompt_fit_preflight
    settings = _settings(context=20480, output=2048)
    for model, exact_tokens in (("qwen3.5:9b", 14622), ("nemotron-3.5-lightning:latest", 14249)):
        result = prompt_fit_preflight(LocalProvider(settings, exact_tokens), [{"role": "user", "content": "frozen package"}], model)
        assert result["count_type"] == "exact"
        assert result["required_total_tokens"] == exact_tokens + 2048
        assert result["passed"] is True
