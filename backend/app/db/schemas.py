from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProjectCreate(BaseModel):
    name: str
    source_type: Literal["github", "zip", "android"]
    repo_url: str | None = None
    android_source_url: str | None = None
    android_case_study: str | None = None
    security_goal: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    source_type: str
    repo_url: str | None
    local_path: str
    commit_hash: str | None
    status: str
    status_message: str | None
    security_goal: str | None
    created_at: str
    updated_at: str


class DiscoverModulesRequest(BaseModel):
    security_goal: str


class WikiGenerateRequest(BaseModel):
    module_path: str
    provider: str = "ollama"
    model: str | None = None


class ChatRequest(BaseModel):
    question: str
    provider: str = "ollama"
    model: str | None = None
    module_id: str | None = None


class CompareRequest(BaseModel):
    question: str
    providers: list[str] = Field(default_factory=lambda: ["ollama"])
    module_id: str | None = None


class VerificationRequest(BaseModel):
    target_type: Literal["chat_message", "wiki_page", "evidence"]
    target_id: str
    verdict: Literal["Verified", "Incomplete", "Incorrect", "Needs Review"]
    human_comment: str | None = None


class ApiResult(BaseModel):
    data: Any
