// READ SUMMARY: This file wraps backend API calls and typed response shapes for the React frontend.
// CHANGED: Added provider availability fields, display_status fields, Ollama model overrides, and HTML audit report downloads.
import type { CompareResult, CompareRunSummary, ExecutionDetails, ModuleCandidate, Project } from "../types";

const API_BASE = "/api";
const TOKEN_KEY = "security_codewiki_token";

export type ModelsHealth = {
  ollama: {
    base_url: string;
    reachable: boolean;
    available: boolean;
    reason: string;
    available_models: string[];
    default_model: string;
    default_model_exists: boolean;
    status: string;
    detail?: string | null;
  };
  openai: { api_key_configured: boolean; default_model_configured: boolean; default_model: string; available: boolean; reason: string; status: string };
  gemini: { api_key_configured: boolean; default_model_configured: boolean; default_model: string; available: boolean; reason: string; status: string };
  groq: { api_key_configured: boolean; default_model_configured: boolean; default_model: string; available_models: string[]; available: boolean; reachable: boolean; reason: string; status: string; default_model_exists?: boolean };
  embedding: { provider: string; model: string; semantic: boolean; fallback_used: boolean; warning?: string | null; label: string };
};

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401) {
    clearToken();
    if (window.location.pathname !== "/login") {
      window.location.assign("/login");
    }
    throw new Error("Your session expired or is invalid. Please log in again.");
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || response.statusText);
  }
  return response.json();
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  health: () => request<{ status: string }>("/health"),
  modelsHealth: () => request<ModelsHealth>("/models/health"),
  projects: () => request<Project[]>("/projects"),
  deleteProject: (id: string) => request<{ deleted: boolean; message: string }>(`/projects/${id}`, { method: "DELETE" }),
  createProject: (payload: unknown) => request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  createZipProject: (form: FormData) => request<Project>("/projects/zip", { method: "POST", body: form }),
  androidCaseStudies: () => request<Array<{ id: string; name: string; hint: string }>>("/projects/android-case-studies"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  status: (id: string) => request<{ status: string; status_message: string; project: Project }>(`/projects/${id}/status`),
  fileTree: (id: string) => request(`/projects/${id}/files/tree`),
  fileContent: (id: string, path: string) => request<{ path: string; content: string; language: string }>(`/projects/${id}/files/content?path=${encodeURIComponent(path)}`),
  discover: (id: string, security_goal: string) =>
    request<ModuleCandidate[]>(`/projects/${id}/discover-security-modules`, {
      method: "POST",
      body: JSON.stringify({ security_goal })
    }),
  generateWiki: (id: string, module_path: string, provider: string, model?: string) =>
    request<{ content_markdown: string; evidence: unknown[]; execution?: ExecutionDetails; validation_status: string; display_status?: string; stored: boolean; failed_draft?: string | null; provider: string; model: string }>(`/projects/${id}/wiki/generate`, {
      method: "POST",
      body: JSON.stringify({ module_path, provider, model })
    }),
  wikiPages: (id: string) => request<Array<{ id: string; title: string; content_markdown: string; module_id: string; updated_at: string }>>(`/projects/${id}/wiki`),
  formalRuns: (id: string) => request<Array<Record<string, unknown>>>(`/projects/${id}/formal-runs`),
  usage: (id: string) => request<any>(`/projects/${id}/usage`),
  deleteWikiPage: (id: string, wikiPageId: string) =>
    request<{ deleted: boolean; message: string }>(`/projects/${id}/wiki/${wikiPageId}`, { method: "DELETE" }),
  chat: (id: string, question: string, provider: string, model?: string, module_id?: string) =>
    request<{ message_id: string; answer: string; evidence: unknown[]; wiki_context: unknown[]; context_used: string; validation_status: string; display_status?: string; provider: string; model: string; execution?: ExecutionDetails }>(`/projects/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ question, provider, model, module_id })
    }),
  compare: (id: string, question: string, providers: string[], module_id?: string, provider_models?: Record<string, string>) =>
    request<{ question: string; evidence: unknown[]; wiki_context: unknown[]; results: CompareResult[]; run_summary: CompareRunSummary; excluded_providers?: Array<{ provider: string; reason: string }>; comparison_valid: boolean; comparison_invalid_reason?: string | null; shared_evidence_package_id?: string; shared_evidence_hash?: string }>(`/projects/${id}/compare-models`, {
      method: "POST",
      body: JSON.stringify({ question, providers, module_id, provider_models })
    }),
  scoreEvaluation: (id: string, evaluationId: string, payload: unknown) =>
    request(`/projects/${id}/evaluations/${evaluationId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  verify: (id: string, target_type: string, target_id: string, verdict: string, human_comment?: string) =>
    request(`/projects/${id}/verify`, {
      method: "POST",
      body: JSON.stringify({ target_type, target_id, verdict, human_comment })
    })
};

export function exportUrl(projectId: string, format: "markdown" | "json" | "csv"): string {
  return `${API_BASE}/projects/${projectId}/export?format=${encodeURIComponent(format)}`;
}

export async function downloadExport(projectId: string, format: "markdown" | "json" | "csv"): Promise<void> {
  const token = getToken();
  const response = await fetch(exportUrl(projectId, format), {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (response.status === 401) {
    clearToken();
    window.location.assign("/login");
    throw new Error("Your session expired or is invalid. Please log in again.");
  }
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename=([^;]+)/);
  const filename = match?.[1] ?? `security-codewiki.${format === "markdown" ? "md" : format}`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function downloadAuditReport(projectId: string): Promise<void> {
  const token = getToken();
  const response = await fetch(`${API_BASE}/projects/${projectId}/export/pdf`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {}
  });
  if (response.status === 401) {
    clearToken();
    window.location.assign("/login");
    throw new Error("Your session expired or is invalid. Please log in again.");
  }
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `audit-report-${projectId}.html`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
