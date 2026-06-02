# READ SUMMARY: This module loads environment-backed backend settings and prepares configured storage directories.
# CHANGED: Added per-provider LLM timeout settings with fast local Ollama defaults.
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_superuser_username: str = "admin"
    app_superuser_password: str = "change_me"
    app_secret_key: str = "local_secret"
    app_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    project_storage_path: str = "./storage/projects"
    chroma_db_path: str = "./storage/chroma"
    export_storage_path: str = "./storage/exports"
    sqlite_db_path: str = "./storage/security_codewiki.db"

    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: float = 60.0

    openai_api_key: str = ""
    openai_default_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 120.0
    gemini_api_key: str = ""
    gemini_default_model: str = "gemini-1.5-flash"
    gemini_timeout_seconds: float = 120.0
    deepseek_api_key: str = ""
    deepseek_default_model: str = "deepseek-chat"
    deepseek_timeout_seconds: float = 120.0
    cloud_llm_timeout_seconds: float = 120.0

    embedding_provider: str = "sentence-transformers"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    max_total_files_to_index: int = 2000
    max_total_chunks_to_index: int = 10000
    max_repo_size_mb: float = 500.0
    git_clone_timeout_seconds: int = 300

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.app_cors_origins.split(",") if origin.strip()]

    @property
    def resolved_gemini_default_model(self) -> str:
        return self.gemini_default_model.strip() or "gemini-2.5-flash"

    def ensure_storage(self) -> None:
        for path in [
            self.project_storage_path,
            self.chroma_db_path,
            self.export_storage_path,
            str(Path(self.sqlite_db_path).parent),
        ]:
            Path(path).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_storage()
    return settings
