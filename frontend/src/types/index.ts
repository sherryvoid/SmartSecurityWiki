export type Project = {
  id: string;
  name: string;
  source_type: "github" | "zip" | "android";
  repo_url?: string | null;
  local_path: string;
  commit_hash?: string | null;
  status: string;
  status_message?: string | null;
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
