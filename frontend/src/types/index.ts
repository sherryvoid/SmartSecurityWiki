// READ SUMMARY: This file defines shared frontend TypeScript types for project, file tree, evidence, and module candidate data.
// CHANGED: Added optional evidence scoring and selected-file preference fields returned by backend query-time retrieval re-ranking.
export type Project = {
  id: string;
  name: string;
  source_type: "github" | "zip" | "android";
  repo_url?: string | null;
  local_path: string;
  subfolder_path?: string | null;
  android_case_study?: string | null;
  commit_hash?: string | null;
  status: string;
  status_message?: string | null;
  progress_percent?: number | null;
  files_indexed?: number | null;
  total_files?: number | null;
  chunks_indexed?: number | null;
  total_chunks?: number | null;
  current_file?: string | null;
  security_goal?: string | null;
  created_at: string;
  updated_at: string;
};

export type FileNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileNode[];
};

export type Evidence = {
  chunk_id: string;
  file_path: string;
  symbol_name?: string | null;
  class_name?: string | null;
  start_line: number;
  end_line: number;
  language: string;
  security_tags?: string | null;
  code_snippet: string;
  critical_lines?: number[];
  base_similarity?: number;
  final_score?: number;
  file_weight?: number;
  chunk_type_weight?: number;
  security_boost?: number;
  selected_file_match?: boolean;
  selected_file_boost?: number;
  lexical_score?: number;
  vector_rank_bonus?: number;
  co_occurrence_boost?: number;
  test_file_penalty?: number;
  file_type_weight?: number;
  http_method?: string | null;
  class_route?: string | null;
  method_route?: string | null;
  effective_route?: string | null;
  class_route_state?: "absent" | "explicit_empty" | "present" | "unavailable";
  method_route_state?: "absent" | "explicit_empty" | "present" | "unavailable";
  route_resolution_status?: "resolved" | "unresolved";
  retrieval_rank?: number;
  prompt_position?: number;
  evidence_priority_class?: "target_primary" | "required_supporting_role" | "route_or_class_context" | "helper_or_execution_context" | "optional_context";
};

export type ExecutionDetails = {
  execution_id: string;
  started_at: string;
  completed_at: string;
  operation: string;
  status: string;
  query: string;
  expanded_query?: string;
  selected_file?: string | null;
  enumeration_intent?: boolean;
  requested_top_k?: number;
  retrieval?: Record<string, unknown>;
  provider?: Record<string, unknown>;
  processing?: Record<string, unknown>;
  error?: { category?: string; safe_message?: string } | null;
};

export type CompareResult = {
  evaluation_id: string;
  provider: string;
  selection_id?: string;
  model: string;
  answer: string;
  full_answer?: string;
  answer_preview?: string;
  latency_ms: number;
  validation_status: string;
  display_status?: string;
  execution?: ExecutionDetails;
  shared_evidence_package_id?: string;
  shared_evidence_hash?: string;
  serialized_chunk_ids?: string[];
  evidence_package_match?: boolean;
  warnings?: Array<{ code: string; claim?: string; message: string }>;
  evaluation_status?: string;
  supplied_source_count?: number;
  cited_source_count?: number;
  usage?: Record<string, any>;
};

export type ModuleCandidate = {
  module_path: string;
  language: string;
  reason: string;
  confidence: string;
  security_tags: string[];
  matching_symbols?: string[];
  matching_chunk_count?: number;
  score: number;
};

export type CompareRunSummary = { execution_id: string; question: string; shared_evidence_package_id: string; shared_evidence_hash: string; comparison_valid: boolean; comparison_invalid_reason?: string | null; started_at: string; completed_at: string; total_duration: number; selected_models: Array<{ provider: string; model: string }> };
