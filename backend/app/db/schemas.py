# READ SUMMARY: This module defines Pydantic request/response schemas used by the FastAPI backend.
# CHANGED: Added user-facing display status mapping plus optional compare provider model overrides.
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


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
    subfolder_path: str | None = None
    security_goal: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    source_type: str
    repo_url: str | None
    local_path: str
    subfolder_path: str | None = None
    commit_hash: str | None
    status: str
    status_message: str | None
    progress_percent: int | None = None
    files_indexed: int | None = None
    total_files: int | None = None
    chunks_indexed: int | None = None
    total_chunks: int | None = None
    current_file: str | None = None
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


class ChatAnswer(BaseModel):
    answer: str
    confidence: Literal["high", "medium", "low"]
    access_control_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    helper_chain: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    needs_review: bool = False
    display_status: str = ""


# validation_status values assigned by the pipeline include:
# valid_json, invalid_json_fallback, valid_with_dropped_invalid_evidence_refs,
# parse_failed, no_evidence, no_source_evidence, timeout, error, valid_json_repaired.
DISPLAY_STATUS_MAP = {
    "valid_json": "Answer verified",
    "valid_simple": "Valid local answer",
    "invalid_json_fallback": "Invalid structured answer",
    "valid_with_dropped_invalid_evidence_refs": "Valid answer (dropped invalid evidence)",
    "parse_failed": "Answer generated (unstructured)",
    "no_evidence": "No evidence retrieved",
    "no_source_evidence": "Insufficient evidence",
    "timeout": "Model timed out",
    "error": "Model error",
    "completed": "Completed",
    "completed_with_warnings": "Completed with warnings",
    "provider_unavailable": "Provider unavailable",
    "provider_timeout": "Provider timed out",
    "empty_response": "Empty response",
    "response_parse_failed": "Response parse failed",
    "schema_validation_failed": "Schema validation failed",
    "evidence_validation_failed": "Evidence validation failed",
    "completed_with_evidence_mismatch": "Completed with evidence mismatch",
    "context_limit_exceeded": "Context limit exceeded",
    "provider_context_incompatible": "Provider context incompatible",
    "wiki_completed": "Wiki completed",
    "wiki_completed_with_warnings": "Wiki completed with warnings",
    "wiki_empty_response": "Wiki empty response",
    "wiki_parse_failed": "Wiki parse failed",
    "wiki_schema_validation_failed": "Wiki schema validation failed",
    "wiki_provider_timeout": "Wiki provider timed out",
    "wiki_provider_unavailable": "Wiki provider unavailable",
}


def display_status_for(validation_status: str | None) -> str:
    return DISPLAY_STATUS_MAP.get(validation_status or "", "Answer generated")


class WikiEntryPoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    file_path: str
    start_line: int
    end_line: int
    description: str
    chunk_id: Optional[str] = None
    source_reference_status: str = "unvalidated"
    source_reference_status: str = "unvalidated"


class WikiACRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    caller: str
    permission_check: str
    file_path: str
    start_line: Optional[int] = None
    chunk_id: Optional[str] = None
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"


class WikiHelper(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    file_path: str
    role: str
    chunk_id: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"


class WikiRequirementTrace(BaseModel):
    model_config = ConfigDict(extra="allow")

    requirement: str
    code_reference: str
    file_path: Optional[str] = None
    chunk_id: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    source_reference_status: str = "unvalidated"


class SecurityWikiSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    module_overview: str
    entry_points: list[WikiEntryPoint]
    access_control_matrix: list[WikiACRow]
    vertical_helpers: list[WikiHelper]
    requirement_traces: list[WikiRequirementTrace]
    limitations: str
    generated_at: Optional[str] = None


class CompareRequest(BaseModel):
    question: str
    providers: list[str] = Field(default_factory=lambda: ["ollama"])
    module_id: str | None = None
    provider_models: dict[str, str] | None = None


class VerificationRequest(BaseModel):
    target_type: Literal["chat_message", "wiki_page", "evidence"]
    target_id: str
    verdict: Literal["Verified", "Incomplete", "Incorrect", "Needs Review"]
    human_comment: str | None = None


class RunPurposeRequest(BaseModel):
    run_purpose: Literal["development", "diagnostic", "formal_evaluation"]
    question_id: str | None = None


class EvaluationScoreRequest(BaseModel):
    correctness: int | None = Field(default=None, ge=0, le=3)
    evidence_discipline: int | None = Field(default=None, ge=0, le=3)
    source_reference_accuracy: int | None = Field(default=None, ge=0, le=2)
    verdict: Literal["Verified", "Incomplete", "Incorrect", "Needs Review"] | None = None
    evidence_quality: int | None = Field(default=None, exclude=True)
    hallucination: bool | None = None
    notes: str | None = None
    correct_file_path: int | None = Field(default=None, exclude=True)
    correct_code_block: int | None = Field(default=None, exclude=True)
    explanation_quality: int | None = Field(default=None, ge=0, le=3)
    completeness: int | None = Field(default=None, ge=0, le=3)
    hallucination_flag: bool | None = None
    usefulness: int | None = Field(default=None, ge=0, le=3)
    evaluator_comment: str | None = None


class ApiResult(BaseModel):
    data: Any
