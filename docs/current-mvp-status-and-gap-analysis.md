# Security CodeWiki — Current MVP Status and Gap Analysis

## 1. Executive Summary sherr

Security CodeWiki is currently a working local MVP that imports source code, indexes files and code chunks, detects candidate security-relevant files, generates a Security Wiki for a selected candidate, and answers audit questions with retrieved source-code evidence. It also supports local Ollama/Qwen and cloud provider calls, model health checks, model comparison, manual audit feedback, and export.

The current system is best described as a combination of:

- repo browser,
- raw-code RAG chatbot,
- Security Wiki assistant,
- partial model comparison/evaluation tool.

It is no longer just a generic repo browser. The implementation has moved toward the intended Security CodeWiki audit assistant direction because chat retrieval is evidence-first, wiki pages are indexed into RAG, evidence cards open exact source locations, and no-evidence chat requests return a safe refusal.

What works well:

- Local FastAPI + React MVP is functional.
- Superuser login is implemented from `.env`.
- GitHub, ZIP, and Android source-link import flows exist.
- File browser and Monaco source viewer work.
- Code chunks store backend-generated line ranges.
- Security keyword/tag detection exists.
- Chroma indexes raw code chunks and generated wiki chunks.
- Chat retrieves source evidence before LLM generation.
- Evidence cards are backend-generated from retrieved code chunks.
- Evidence cards include file path, line range, snippet, tags, and critical lines.
- Ollama/OpenAI/Gemini/DeepSeek providers exist.
- Provider health/status is exposed.
- Structured chat validation has been added for evidence IDs.
- Manual feedback and comparison scoring are evaluation/reporting features only.

What is still missing before thesis-ready:

- Parser is heuristic regex/fallback, not a robust tree-sitter implementation.
- Python `.py` files are not currently included in indexing, so FastAPI/Python RBAC support is incomplete.
- Horizontal access-control matrices and vertical helper chains are LLM-generated from retrieved snippets, not computed by static analysis.
- Security Wiki generation is Markdown-prompt based, not a fully validated structured wiki schema.
- Discovery returns candidate files grouped by tags/symbols, not true architectural modules.
- Large repository handling is guarded but not fully scalable.
- Frontend tests are missing.
- Export is useful but not yet a polished thesis audit report.

Overall, the MVP still follows the original direction. It has not drifted into a generic chatbot, but several thesis-critical pieces remain partial because they rely on heuristics and LLM interpretation instead of stronger static analysis.

## 2. Original Plan Summary

The original plan is to build a local web-based MVP for evidence-backed LLM and RAG-assisted access-control program comprehension in open-source software. The intended user is a security auditor or researcher who needs to inspect how access-control logic is implemented in a repository.

The intended workflow is:

1. Log in as a local superuser.
2. Import a public GitHub repo, ZIP, or selected Android case-study source.
3. Browse files.
4. Parse code into chunks with backend-generated line ranges.
5. Detect candidate access-control/security files.
6. Let the auditor select a target candidate module/file.
7. Generate a Security Wiki for that target.
8. Index raw code and wiki chunks into RAG.
9. Ask questions.
10. Retrieve source-code evidence first.
11. Send only that evidence package and optional wiki context to the selected LLM.
12. Return answers with file paths, line ranges, snippets, explanation, and limitations.
13. Let the auditor open evidence in the source viewer.
14. Optionally compare models using the same evidence package.
15. Export an audit report.

The evidence-first principle is the center of the design: no source-code evidence, no answer.

The Security Wiki is intended to be an audit artifact and orientation layer for a selected target. It should summarize access-control behavior with evidence references.

RAG is intended to retrieve raw code evidence and generated wiki context. Raw code is proof; wiki context is orientation.

The LLM is intended to explain evidence, not invent source facts. It should not create file paths, line numbers, methods, permissions, or call chains that were not retrieved.

Model comparison is intended for evaluation: send the same evidence package to multiple providers and compare answer quality.

Manual audit feedback is only for audit/evaluation/reporting. It is not self-training and does not automatically improve any model.

## 3. Current Tech Stack

Frontend:

- React `^18.3.1`, TypeScript `^5.7.2`, Vite `^6.0.5`: [frontend/package.json](../frontend/package.json)
- React Router `^7.1.1`: [frontend/src/App.tsx](../frontend/src/App.tsx)
- Monaco editor via `@monaco-editor/react`: [frontend/src/pages/ProjectWorkspace.tsx](../frontend/src/pages/ProjectWorkspace.tsx)
- Icons via `lucide-react`.

Backend:

- FastAPI `0.115.6`: [backend/requirements.txt](../backend/requirements.txt), [backend/app/main.py](../backend/app/main.py)
- Uvicorn for local server.
- Pydantic/Pydantic Settings for schemas/config.

Database:

- SQLite metadata database created from SQL in [backend/app/db/database.py](../backend/app/db/database.py).
- Default path: `./storage/security_codewiki.db` from [backend/app/core/config.py](../backend/app/core/config.py).

Vector database:

- ChromaDB `0.6.3`: [backend/app/services/vector_index.py](../backend/app/services/vector_index.py)
- Default path: `./storage/chroma`.

Parser/chunker:

- Heuristic regex parser and fallback line-range chunker: [backend/app/services/parser.py](../backend/app/services/parser.py)
- Tree-sitter packages are installed in requirements, but current parser code does not use tree-sitter.

Embedding approach:

- Default semantic local provider: `sentence-transformers`, model `BAAI/bge-small-en-v1.5`.
- Fallback hash embeddings: [backend/app/services/vector_index.py](../backend/app/services/vector_index.py)
- Health/status available through [backend/app/services/model_health.py](../backend/app/services/model_health.py).

LLM providers:

- Local Ollama/Qwen, OpenAI, Gemini, DeepSeek: [backend/app/services/llm.py](../backend/app/services/llm.py)

Authentication:

- Single env-configured superuser with JWT bearer token: [backend/app/api/routes/auth.py](../backend/app/api/routes/auth.py), [backend/app/core/security.py](../backend/app/core/security.py)
- No users table and no multi-user registration.

Storage folders:

- Repos: `PROJECT_STORAGE_PATH`, default `./storage/projects`
- Chroma: `CHROMA_DB_PATH`, default `./storage/chroma`
- SQLite: `SQLITE_DB_PATH`, default `./storage/security_codewiki.db`
- Exports: `EXPORT_STORAGE_PATH`, default `./storage/exports`, currently mostly reserved because exports stream from API.

## 4. Current Project Architecture

### 4.1 Frontend Architecture

Main files:

- [frontend/src/main.tsx](../frontend/src/main.tsx): React app bootstrap.
- [frontend/src/App.tsx](../frontend/src/App.tsx): route shell, protected routes, topbar, logout.
- [frontend/src/services/api.ts](../frontend/src/services/api.ts): API client, token handling, model health types, export download.
- [frontend/src/types/index.ts](../frontend/src/types/index.ts): frontend types for projects, file tree, evidence, discovery candidates.
- [frontend/src/styles.css](../frontend/src/styles.css): layout, cards, evidence highlighting, status banners.

Login page:

- [frontend/src/pages/LoginPage.tsx](../frontend/src/pages/LoginPage.tsx)
- Collects username/password.
- Calls `/api/auth/login`.
- Stores JWT in local storage.

Project import page:

- [frontend/src/pages/ProjectsPage.tsx](../frontend/src/pages/ProjectsPage.tsx)
- Supports GitHub URL, ZIP upload, and Android source/case-study link.
- Displays a large-repo warning for full Android AOSP.
- Lists existing projects.

Project workspace:

- [frontend/src/pages/ProjectWorkspace.tsx](../frontend/src/pages/ProjectWorkspace.tsx)
- Main audit screen with sidebar, source viewer, and audit panel.

File tree:

- Built from `/api/projects/{id}/files/tree`.
- Rendered by `FileTree` in [ProjectWorkspace.tsx](../frontend/src/pages/ProjectWorkspace.tsx).

Monaco/code viewer:

- Uses `Editor` from `@monaco-editor/react`.
- Opens source from `/api/projects/{id}/files/content`.
- Supports evidence range and critical-line decorations.

Discovery UI:

- Labelled "Candidate Security Files / Modules".
- Takes optional audit goal.
- Calls `/api/projects/{id}/discover-security-modules`.
- Shows path, language, confidence, reason, security tags, matching symbols, matching chunk count.

Selected module/candidate behavior:

- `selectedModule` is currently a file path, not a row in a modules table.
- It focuses retrieval for wiki/chat/comparison.

Security Wiki UI:

- Manual "Generate Wiki" button.
- Calls `/api/projects/{id}/wiki/generate`.
- Displays Markdown output in the audit panel.

Chat UI:

- Evidence Chat form asks a question.
- Calls `/api/projects/{id}/chat`.
- Shows answer, context used, validation status, and warning for invalid JSON fallback.

Evidence cards:

- Rendered from backend evidence.
- Clicking opens the source file and highlights line range/critical lines.

Model selector:

- Provider selector supports Ollama, Gemini, OpenAI, DeepSeek.
- Optional model override.
- Shows provider health/readiness and embedding status.

Model comparison UI:

- Compare button calls `/api/projects/{id}/compare-models`.
- Shows side-by-side-ish cards in the audit panel.
- Includes manual scoring controls per evaluation.

Verification/manual feedback UI:

- Buttons: Verified, Incomplete, Incorrect, Needs Review.
- Calls `/api/projects/{id}/verify`.
- These are audit feedback labels only, not training signals.

Export UI:

- Buttons for Markdown, JSON, CSV.
- Uses API streaming download from [frontend/src/services/api.ts](../frontend/src/services/api.ts).

### 4.2 Backend Architecture

FastAPI app entry point:

- [backend/app/main.py](../backend/app/main.py)
- Loads settings, initializes DB, configures CORS, registers routers.

Route files:

- [backend/app/api/routes/auth.py](../backend/app/api/routes/auth.py): login.
- [backend/app/api/routes/health.py](../backend/app/api/routes/health.py): `/api/health`.
- [backend/app/api/routes/models.py](../backend/app/api/routes/models.py): `/api/models/health`.
- [backend/app/api/routes/projects.py](../backend/app/api/routes/projects.py): project import, status, files, discovery, wiki, chat, compare, verify, export.

Services:

- [backend/app/services/project_service.py](../backend/app/services/project_service.py): project creation/import/indexing, file tree/content, discovery, evidence retrieval, wiki retrieval, critical lines.
- [backend/app/services/files.py](../backend/app/services/files.py): file filtering, language detection, safe path access, file tree construction.
- [backend/app/services/parser.py](../backend/app/services/parser.py): regex/fallback chunking.
- [backend/app/services/security_detection.py](../backend/app/services/security_detection.py): security keyword/tag detection and confidence.
- [backend/app/services/vector_index.py](../backend/app/services/vector_index.py): embeddings, Chroma indexing/query, wiki chunk indexing.
- [backend/app/services/llm.py](../backend/app/services/llm.py): Ollama/OpenAI/Gemini/DeepSeek providers.
- [backend/app/services/model_health.py](../backend/app/services/model_health.py): provider and embedding readiness.
- [backend/app/services/audit_service.py](../backend/app/services/audit_service.py): wiki generation, chat, comparison, verification, export.

Database layer:

- [backend/app/db/database.py](../backend/app/db/database.py): SQLite connection, schema, migration helper.
- [backend/app/db/schemas.py](../backend/app/db/schemas.py): request/response Pydantic schemas.
- [backend/app/db/models.py](../backend/app/db/models.py): currently not central to persistence.

Config:

- [backend/app/core/config.py](../backend/app/core/config.py): `.env` settings and storage paths.

Auth/security:

- [backend/app/core/security.py](../backend/app/core/security.py): JWT creation and route dependency.

### 4.3 Storage Architecture

Repos are cloned/extracted to:

- `PROJECT_STORAGE_PATH`, default `./storage/projects`
- Each project gets a UUID folder and a `repo` subfolder.
- Implemented in `create_project` and `create_project_from_zip` in [backend/app/services/project_service.py](../backend/app/services/project_service.py).

Metadata is stored in:

- SQLite DB at `SQLITE_DB_PATH`, default `./storage/security_codewiki.db`.
- Tables are created in [backend/app/db/database.py](../backend/app/db/database.py).

Wiki pages:

- Stored in SQLite table `wiki_pages`.
- Project folders also create a `wiki` directory, but current wiki page content is stored in DB.

Chroma vectors:

- Stored at `CHROMA_DB_PATH`, default `./storage/chroma`.
- Implemented with `chromadb.PersistentClient` in [backend/app/services/vector_index.py](../backend/app/services/vector_index.py).

Exports:

- Export content is streamed from API routes in [backend/app/services/audit_service.py](../backend/app/services/audit_service.py) and [backend/app/api/routes/projects.py](../backend/app/api/routes/projects.py).
- `EXPORT_STORAGE_PATH` exists in config but current exports are not saved there, so `storage/exports` may be empty.

Why folders may appear empty:

- `projects` is empty until a repo/ZIP is imported.
- `chroma` is empty until chunks are indexed.
- `exports` is empty because exports stream directly.
- `backend/storage` vs root `storage` depends on where the backend process is started. Relative paths are resolved from the backend working directory. Starting from `backend` creates `backend/storage`; starting from repo root could create `storage`.

## 5. Current Database Schema

Schema source: [backend/app/db/database.py](../backend/app/db/database.py)

### `projects`

Important columns:

- `id`
- `name`
- `source_type`
- `repo_url`
- `local_path`
- `commit_hash`
- `status`
- `status_message`
- `security_goal`
- `created_at`
- `updated_at`

Stores project metadata and indexing/import status. Persists until the SQLite DB or rows are deleted.

### `files`

Important columns:

- `id`
- `project_id`
- `file_path`
- `language`
- `size_bytes`
- `line_count`
- `is_indexed`
- `created_at`

Stores indexed source/config/document files for a project. Relates to `projects.id` through `project_id`.

### `code_chunks`

Important columns:

- `id`
- `project_id`
- `file_id`
- `chunk_type`
- `symbol_name`
- `class_name`
- `start_line`
- `end_line`
- `code`
- `security_tags`
- `embedding_id`
- `created_at`

Stores backend-generated code chunks and line ranges. Relates to `files.id` through `file_id`.

### `wiki_pages`

Important columns:

- `id`
- `project_id`
- `module_id`
- `title`
- `slug`
- `content_markdown`
- `created_at`
- `updated_at`

Stores generated Security Wiki Markdown for a selected candidate path. `module_id` is currently a selected file path, not a real module table ID.

### `chat_sessions`

Important columns:

- `id`
- `project_id`
- `module_id`
- `model_provider`
- `model_name`
- `created_at`

Stores a chat session per question/answer flow.

### `chat_messages`

Important columns:

- `id`
- `session_id`
- `role`
- `content`
- `evidence_json`
- `raw_model_response`
- `parsed_answer_json`
- `validation_status`
- `created_at`

Stores user and assistant messages. Assistant messages include backend evidence JSON and structured validation data when available.

### `evaluations`

Important columns:

- `id`
- `project_id`
- `module_path`
- `question`
- `model_provider`
- `model_name`
- `answer_text`
- `parsed_answer_json`
- `evidence_json`
- `wiki_context_json`
- `validation_status`
- `latency_ms`
- `estimated_cost`
- manual scoring fields: `correct_file_path`, `correct_code_block`, `explanation_quality`, `completeness`, `hallucination_flag`, `usefulness`, `evaluator_comment`

Stores model comparison outputs and manual evaluation scores.

### `verifications`

Important columns:

- `id`
- `target_type`
- `target_id`
- `verdict`
- `human_comment`
- `created_at`

Stores manual audit verdicts for chat messages, wiki pages, or evidence. This is not self-training.

Answers:

- There is no real users table.
- There is no real modules table.
- Manual feedback is stored in `verifications`; comparison scoring is stored in `evaluations`.
- Feedback is not used for self-training.
- Developer can open the SQLite DB at `backend/storage/security_codewiki.db` if the backend is started from the `backend` directory with default settings.

## 6. Current End-to-End Pipeline

### Step 1 — Login

Login is handled by:

- Frontend: [frontend/src/pages/LoginPage.tsx](../frontend/src/pages/LoginPage.tsx)
- Backend route: [backend/app/api/routes/auth.py](../backend/app/api/routes/auth.py)
- Security helper: [backend/app/core/security.py](../backend/app/core/security.py)

Credentials come from `.env` via [backend/app/core/config.py](../backend/app/core/config.py):

- `APP_SUPERUSER_USERNAME`
- `APP_SUPERUSER_PASSWORD`
- `APP_SECRET_KEY`

This is only an env-based superuser flow with JWT. There is no full authentication module, user registration, password hashing, roles, or users table.

### Step 2 — Project Creation / Import

GitHub import:

- Frontend: [frontend/src/pages/ProjectsPage.tsx](../frontend/src/pages/ProjectsPage.tsx)
- Route: `POST /api/projects` in [backend/app/api/routes/projects.py](../backend/app/api/routes/projects.py)
- Service: `create_project`, `import_and_index_project` in [backend/app/services/project_service.py](../backend/app/services/project_service.py)
- Runs as FastAPI `BackgroundTasks` for GitHub and Android source links.

ZIP import:

- Route: `POST /api/projects/zip`
- Service: `create_project_from_zip`
- ZIP upload/extraction/indexing happens inside the request flow, so it is more synchronous than GitHub import.

Android source/case-study mode:

- Frontend shows case-study selector and Android project link.
- Backend route `GET /api/projects/android-case-studies` returns static case study hints.
- Android import currently behaves like source-link import. It does not clone full AOSP with specialized logic.

### Step 3 — Repo Fetching / Extraction

Git clone:

- Implemented in `_clone_repository` in [backend/app/services/project_service.py](../backend/app/services/project_service.py).
- Uses `git clone --depth 1`.
- Has timeout from `GIT_CLONE_TIMEOUT_SECONDS`, default `300`.
- On failure or timeout, `import_and_index_project` marks project `failed` with status message.

ZIP extraction:

- Implemented in `_safe_extract_zip`.
- Checks path traversal before extraction.
- Invalid ZIP or unsafe path fails the import.

### Step 4 — File Filtering and File Indexing

File filtering source: [backend/app/services/files.py](../backend/app/services/files.py)

Included extensions:

- `.java`
- `.go`
- `.cpp`
- `.c`
- `.h`
- `.aidl`
- `.xml`
- `.json`
- `.yaml`
- `.yml`
- `.te`
- `.md`
- `.txt`

Ignored folders:

- `.git`
- `node_modules`
- `build`
- `dist`
- `out`
- `target`
- `.idea`
- `.vscode`

Python `.py` files are currently not included. This is a gap for FastAPI/Python RBAC case studies.

File size limit:

- `MAX_FILE_BYTES = 1_000_000`

Indexing stores file metadata in `files` and chunk metadata in `code_chunks`.

### Step 5 — Parsing and Chunking

Parser source: [backend/app/services/parser.py](../backend/app/services/parser.py)

Chunks are created by:

- Markdown headings for Markdown.
- Regex symbol detection for Java/C/C++/Go-ish files.
- `GO_FUNCTION_PATTERN` for Go functions.
- Fallback fixed line ranges of 80 lines.

Tree-sitter is installed in `requirements.txt`, but the current parser implementation does not use it. This should be marked Partial/Missing for the original tree-sitter goal.

Line numbers are generated by enumerating source lines in Python. `start_line` and `end_line` are stored in DB and used by evidence cards.

Parsing reliability:

- Good enough for simple Java/Go/C-like functions.
- Weak for annotations, multiline signatures, lambdas, nested classes, Kotlin, Python, complex C++, and Android framework edge cases.
- If parsing fails, fallback chunks preserve line ranges but lose method/function names.

### Step 6 — Security Tag Detection

Security detection source: [backend/app/services/security_detection.py](../backend/app/services/security_detection.py)

Keywords include:

- `permission`
- `authorize`
- `authorization`
- `authentication`
- `access`
- `role`
- `policy`
- `SecurityException`
- `checkPermission`
- `enforcePermission`
- `getCallingUid`
- `getCallingUserId`
- `hasSignatureCapability`
- `Binder`
- `SELinux`
- `RBAC`
- `SubjectAccessReview`
- `AccessDenied`
- `Forbidden`
- `hasPermission`
- `hasRole`
- `hasAuthority`
- `authenticated`
- `requestMatchers`
- `antMatchers`
- `permitAll`
- `denyAll`

Tags include:

- `potential_access_check`
- `potential_policy_file`
- `potential_entry_point`
- `potential_helper`
- `potential_config_file`

Confidence is simple:

- 3+ tags: High
- 1+ tags: Medium
- 0 tags: Low

### Step 7 — RAG / Chroma Indexing

Vector index source: [backend/app/services/vector_index.py](../backend/app/services/vector_index.py)

Chroma is used as a persistent vector DB per project collection.

Embedding provider:

- Default: `sentence-transformers` with `BAAI/bge-small-en-v1.5`
- Fallback: hash embeddings if semantic model/package fails
- Status returned by `/api/models/health`

Raw code chunks are indexed with metadata:

- `source_type=code`
- `chunk_id`
- `project_id`
- `file_path`
- `symbol_name`
- `start_line`
- `end_line`
- `language`
- `security_tags`

Generated wiki chunks are indexed with metadata:

- `source_type=wiki`
- `project_id`
- `wiki_page_id`
- `module_id`
- `title`
- `section_title`
- `chunk_index`
- `created_at`

Index rebuild:

- Route: `POST /api/projects/{project_id}/rebuild-index`
- Re-runs `index_project` in a background task.

### Step 8 — Discovery

Discovery route:

- `POST /api/projects/{project_id}/discover-security-modules`
- Service: `discover_security_modules` in [backend/app/services/project_service.py](../backend/app/services/project_service.py)

Discovery input:

- User can type audit goal terms such as "role based access control endpoints" or "account token permission checks".

Discovery does not use an LLM. It uses backend scoring:

- file path,
- chunk tags,
- symbol names,
- audit goal term matches.

It returns candidate files, not true modules. Results may be called modules because the UI uses "Candidate Security Files / Modules" for the MVP concept. The selected module is currently just a file path.

Discovery affects:

- Wiki generation: generated for selected candidate path.
- Chat: retrieval is focused on selected path when present.
- Comparison: same selected path can focus the shared evidence package.

### Step 9 — Security Wiki Generation

Wiki route:

- `POST /api/projects/{project_id}/wiki/generate`
- Service: `generate_wiki` in [backend/app/services/audit_service.py](../backend/app/services/audit_service.py)

Wiki generation is manual, not automatic.

This is the project's own generated Security Wiki. Google Code Wiki is not used anywhere in the current codebase.

Wiki is generated for a selected candidate file/module path, not the whole project.

Input evidence:

- `retrieve_evidence(project_id, request.module_path, request.module_path, limit=12)`
- This focuses evidence on the selected file path.

LLM prompt:

- Asks for Markdown sections: Module Security Overview, Public Entry Points, Horizontal Access-Control Matrix, Vertical Helper Analysis, Requirement-to-Code Traces, Evidence Blocks, Human Auditor Notes, Needs Review.

Output:

- Markdown.
- Stored in `wiki_pages`.
- Indexed into Chroma as `source_type=wiki`.
- Not fully schema-validated.

Chat uses wiki context after generation through `retrieve_wiki_context`, but wiki context is orientation only, not source proof.

Wiki generation may take time because it performs retrieval and then waits for the selected LLM. Local Ollama/Qwen may cold-start and load the model into memory.

### Step 10 — AI Chat

Chat route:

- `POST /api/projects/{project_id}/chat`
- Service: `chat` in [backend/app/services/audit_service.py](../backend/app/services/audit_service.py)

When the user asks a question:

1. Backend retrieves raw source-code evidence.
2. Backend retrieves wiki context if available.
3. If no raw source evidence exists, it returns `Not verified from the available source-code evidence.`
4. If evidence exists, the prompt sends:
   - source-code evidence section,
   - wiki context section,
   - strict JSON instruction with evidence IDs.

Retrieval scope:

- Whole project by default.
- Focused to selected candidate file if `module_id` is provided.

Source-code evidence and wiki context are separated in the prompt. Wiki is explicitly orientation only.

Answer format:

- Current chat asks for structured JSON.
- Backend parses with Pydantic `ChatAnswer`.
- Invalid JSON falls back to raw model text with `validation_status=invalid_json_fallback`.

Hallucination risk:

- Reduced because evidence cards are backend-generated and evidence refs are validated.
- Still possible in prose when JSON fallback happens, or if the model explains beyond retrieved evidence.

Evidence snippets come from stored backend chunks, not the LLM.

### Step 11 — Evidence Cards and Code Viewer

Evidence card fields:

- `chunk_id`
- `file_path`
- `symbol_name`
- `class_name`
- `start_line`
- `end_line`
- `language`
- `security_tags`
- `code_snippet`
- `critical_lines`

Evidence cards are built in `retrieve_evidence` in [backend/app/services/project_service.py](../backend/app/services/project_service.py).

Clicking an evidence card:

- Calls `openFile` in [frontend/src/pages/ProjectWorkspace.tsx](../frontend/src/pages/ProjectWorkspace.tsx).
- Loads file content from backend.
- Opens in Monaco.
- Scrolls to `start_line`.
- Highlights full evidence range.
- Highlights critical lines more strongly.

Critical-line keywords are listed in `CRITICAL_KEYWORDS` in [backend/app/services/project_service.py](../backend/app/services/project_service.py).

### Step 12 — Model Provider Flow

Ollama/Qwen:

- Configured with `OLLAMA_BASE_URL` and `OLLAMA_DEFAULT_MODEL`.
- Default model is `qwen3.5:9b` in [backend/app/core/config.py](../backend/app/core/config.py).
- Ollama call uses `/api/chat` in [backend/app/services/llm.py](../backend/app/services/llm.py).

OpenAI:

- Uses `OPENAI_API_KEY` and `OPENAI_DEFAULT_MODEL`.
- Calls OpenAI chat completions endpoint.

Gemini:

- Uses `GEMINI_API_KEY` and `GEMINI_DEFAULT_MODEL`.
- Calls Google Generative Language API.

DeepSeek:

- Uses `DEEPSEEK_API_KEY` and `DEEPSEEK_DEFAULT_MODEL`.
- Calls DeepSeek chat completions endpoint.

API keys are stored in backend `.env` and are not exposed to frontend.

Health checks:

- `GET /api/models/health`
- Checks Ollama `/api/tags`.
- Checks whether cloud API keys and default models are configured.
- Includes embedding provider status.

Model override:

- Frontend has optional model override.
- Model choice affects generation only, not retrieval or embeddings.

### Step 13 — Model Comparison

Comparison route:

- `POST /api/projects/{project_id}/compare-models`
- Service: `compare_models` in [backend/app/services/audit_service.py](../backend/app/services/audit_service.py)

It retrieves evidence once, then sends the same evidence package to each selected provider.

Answers are persisted in `evaluations` with:

- project/module/question,
- provider/model,
- answer text,
- parsed JSON if valid,
- evidence JSON,
- wiki context JSON,
- validation status,
- latency.

Human scoring UI exists in [frontend/src/pages/ProjectWorkspace.tsx](../frontend/src/pages/ProjectWorkspace.tsx), with fields for file path correctness, code block correctness, explanation quality, completeness, hallucination flag, usefulness, and evaluator comment.

What is still missing:

- More polished side-by-side comparison layout.
- Better cost estimation.
- Aggregated evaluation dashboard.
- Export formatting for comparison could be improved.

### Step 14 — Manual Audit Feedback

Verification buttons call:

- `POST /api/projects/{project_id}/verify`
- Service: `verify` in [backend/app/services/audit_service.py](../backend/app/services/audit_service.py)

Feedback is stored in `verifications`.

Comparison scoring is stored in `evaluations`.

Feedback may appear in exports. It does not self-train the model, update embeddings, tune prompts automatically, or change retrieval.

### Step 15 — Export

Export route:

- `GET /api/projects/{project_id}/export/{format}`
- Formats: Markdown, JSON, CSV.

Included data:

- project metadata,
- wiki pages,
- chat messages,
- evaluations,
- verifications,
- limitations.

Exports stream from the API response. They are not saved to disk by default, which is why `exports` folder may be empty.

Missing for polished thesis report:

- Better sectioning and formatting.
- Stronger inclusion of selected module and evidence cards.
- Better comparison tables.
- Better manual scoring summary.
- Optional saved report history.

## 7. Current Large Repository Handling

Current large repo behavior:

- GitHub import uses full repo path but shallow clone `--depth 1`.
- Clone timeout exists: `GIT_CLONE_TIMEOUT_SECONDS`, default `300`.
- File size limit exists: `MAX_FILE_BYTES = 1_000_000`.
- Project-level guardrails exist in [backend/app/core/config.py](../backend/app/core/config.py):
  - `MAX_TOTAL_FILES_TO_INDEX=2000`
  - `MAX_TOTAL_CHUNKS_TO_INDEX=10000`
  - `MAX_REPO_SIZE_MB=500`
- Indexing stops with partial status message if file/chunk limits are reached.
- Repo size over limit marks project failed.

Missing or partial:

- No pre-clone repo size check.
- No selected subfolder clone/import yet.
- No pagination for file tree/discovery.
- No dedicated indexing queue beyond FastAPI background tasks.
- No cancellation/retry UI.
- No detailed progress percentage.
- Chroma indexing could be slow for many chunks.
- LLM prompt size is controlled by retrieval limit but large repos may still produce noisy retrieval.

Full Android AOSP is not realistic yet. Android support currently means selected source/case-study link, not full AOSP import and analysis.

Recommended large-repo strategy:

- Use selected Android case-study packages.
- Add selected subfolder import.
- Keep repo size warning.
- Add proper background job table and progress tracking.
- Add cancellation/retry.
- Use lazy/incremental embeddings.
- Enforce file/chunk limits.
- Add pagination and search in file tree.
- Use target-first analysis before indexing everything.

## 8. Performance and Timeout Analysis

Synchronous operations:

- Chat is synchronous.
- Wiki generation is synchronous.
- Model comparison is synchronous and loops through providers.
- ZIP extraction/indexing is currently in the ZIP request path.

Background operations:

- GitHub and Android source-link import/indexing use FastAPI `BackgroundTasks`.
- Rebuild index uses `BackgroundTasks`.

Timeout settings:

- Ollama timeout: `OLLAMA_TIMEOUT_SECONDS`, default `300`, in [backend/app/core/config.py](../backend/app/core/config.py) and [backend/app/services/llm.py](../backend/app/services/llm.py).
- Cloud LLM timeout: `CLOUD_LLM_TIMEOUT_SECONDS`, default `120`.
- Git clone timeout: `GIT_CLONE_TIMEOUT_SECONDS`, default `300`.
- Ollama health check timeout: `5` seconds in [backend/app/services/model_health.py](../backend/app/services/model_health.py).

There is a 300-second timeout for Ollama generation and Git clone by default.

Why first chat/wiki may take long:

- Ollama may cold-start and load Qwen into memory.
- The selected local model may be large.
- Semantic embedding model may load on first indexing/query path.
- Chroma collection may initialize.
- Evidence prompt may be long.
- Cloud APIs may have latency or rate limits.

Frontend loading feedback:

- Workspace sets `busy` messages for opening files, discovery, wiki, chat, compare, and verification.

Backend timing logs:

- Basic logging exists.
- Detailed stage-level timing logs are not implemented.

## 9. Alignment With Original Plan — Side-by-Side Table

| Original Planned Feature | Current Status | Current Implementation | Deviation / Problem | Impact on Thesis Goal | Recommended Fix |
|---|---|---|---|---|---|
| Local web MVP | Done | React/Vite frontend + FastAPI backend | None major | Supports demo | Keep local-first |
| Superuser login from .env | Done | [auth.py](../backend/app/api/routes/auth.py), [security.py](../backend/app/core/security.py) | No real users | Fine for MVP | Keep simple |
| Public GitHub import | Done | `POST /api/projects`, shallow clone | No subfolder clone | Large repos risky | Add subfolder import |
| ZIP upload | Done | `POST /api/projects/zip`, safe extraction | Synchronous indexing | Large ZIP may block | Move to job queue |
| Android selected case-study/source import | Partial | Static case-study hints + source link | No real AOSP/case package manager | Android thesis demos limited | Curated Android sample packages |
| File tree and source viewer | Done | File tree + Monaco | No pagination | Large trees may be slow | Add search/pagination |
| Method/function parsing with line ranges | Partial | Regex/fallback chunks | Not robust | Evidence ranges can be coarse | Use tree-sitter |
| Tree-sitter parser | Missing | Packages installed only | Not used | Weak parsing for thesis | Implement tree-sitter parsers |
| Python/FastAPI support | Missing | `.py` not included | Cannot inspect Python RBAC | Limits case studies | Add `.py` and Python chunker |
| Java/Spring support | Partial | `.java`, Spring security keywords | Regex parser only | Works for simple cases | Add Java tree-sitter |
| Go/Kubernetes support | Partial | `.go`, Go function regex | No Kubernetes-specific policy logic | Limited K8s analysis | Add Go tree-sitter and K8s patterns |
| Android Java/AIDL/C++/SELinux support | Partial | `.java`, `.aidl`, `.cpp`, `.c`, `.h`, `.te` | No Android-specific parser/call graph | Android analysis shallow | Add Android-focused samples and patterns |
| Security candidate detection | Done/Partial | Keyword tags | Heuristic | Good MVP discovery, not proof | Add richer detectors |
| Discovery as candidate module/file selection | Partial | Candidate files called modules | No real modules table | Conceptual ambiguity | Keep wording clear or add module model later |
| Security Wiki generation | Done/Partial | LLM-generated Markdown | Not structured/validated | Useful but variable | Structured wiki JSON |
| Security Wiki structured JSON | Missing | Markdown prompt only | No wiki schema validation | Weak consistency | Add Pydantic wiki schema |
| Security Wiki stored | Done | `wiki_pages` table | None major | Audit artifact exists | Improve schema |
| Security Wiki indexed into RAG | Done | `source_type=wiki` chunks | Wiki is orientation only | Good | Keep separated from proof |
| Raw code RAG | Done | Chroma code chunks | Retrieval is simple hybrid-ish | Core works | Improve ranking |
| Semantic embeddings | Done/Partial | sentence-transformers default | May fall back silently with warning | Thesis-grade if active | Ensure setup verifies semantic model |
| Hash embedding fallback | Done | Hash provider | Not thesis-grade | Dev-only | Label fallback clearly |
| Evidence-first chat | Done | Retrieves evidence before LLM | Retrieval quality still heuristic | Core thesis goal supported | Improve retrieval |
| No evidence, no answer | Done | Safe refusal string | Provider error wording sometimes mentions evidence | Good | Keep strict |
| Code snippets in chat | Done | Evidence cards/snippets | Prose may not quote all | Good | Fine |
| Evidence cards open code viewer | Done | Monaco open/highlight | None major | Strong auditor UX | Keep |
| Critical line highlighting | Done | Keyword critical lines | Heuristic | Helpful, not proof | Expand detector |
| Structured JSON chat response | Partial/Done | `ChatAnswer` validation | Fallback if invalid JSON | Safer but not perfect | Add repair parse/testing |
| LLM hallucination control | Partial | Evidence ID validation | Prose fallback can hallucinate | Important risk | Strict structured output and post-checks |
| Local Qwen/Ollama integration | Done | Ollama provider | Requires local model running | Good | Improve UX for model install |
| OpenAI integration | Done | API key provider | Key required | Good | Add better errors |
| Gemini integration | Done | API key provider | Key required | Good | Add better errors |
| DeepSeek integration | Done | API key provider | Key required | Good | Add better errors |
| Model health checks | Done | `/api/models/health` | Basic only | Good | Add test coverage |
| Model comparison | Done/Partial | Same evidence sent to providers | Sequential, not polished | Useful evaluation | Improve UI/layout |
| Model comparison persistence | Done | `evaluations` table | Basic export | Good | Add dashboard |
| Human/manual audit feedback | Done | `verifications`, `evaluations` scores | No feedback history UI | Good | Add comments everywhere |
| Exportable audit report | Partial | Markdown/JSON/CSV streamed | Basic formatting | Needs supervisor-ready polish | Improve report template |
| Large repo strategy | Partial | Limits/timeouts/warning | No subfolder/lazy jobs | Large repo risk remains | Add selected subfolder and jobs |
| Tests | Partial | Backend pytest tests exist | No frontend/integration depth | Confidence limited | Add broader test suite |
| Documentation/README | Partial | README updated | More docs needed | Usable | Keep report/docs updated |

## 10. Mapping to Professor’s Report and Thesis Methodology

### 10.1 Horizontal Access-Control Matrix

The system can ask the LLM to generate a horizontal access-control matrix in the Security Wiki prompt. It is LLM-generated from retrieved snippets, not computed by static analysis. Reliability depends on retrieval quality and model compliance. Missing: structured matrix schema, endpoint/resource extraction, role/permission normalization, and validation against evidence.

### 10.2 Vertical Helper Analysis

The system asks for vertical helper analysis in the wiki prompt and chat can answer helper-chain questions from retrieved evidence. There is no real call graph support. Helper chains are inferred by the LLM from retrieved snippets. Missing: static call graph, interprocedural search, symbol references, and validated helper trace output.

### 10.3 Requirement-to-Code Trace

Users can ask requirement-style questions such as "Where is admin access enforced?" The system retrieves evidence and answers. It does not truly trace formal requirements to code. Missing: requirement objects, trace table, coverage status, and explicit requirement-to-evidence schema.

### 10.4 Permission / Policy Mapping

The system detects permissions/policies through keywords and file types. It has partial support for Android terms, SELinux `.te`, Spring roles/matchers, and general access-control words. Python/FastAPI dependencies are not supported because `.py` files are not indexed. Missing: Android permission manifest parsing, SELinux rule interpretation, Spring route extraction, FastAPI dependency parsing, and policy graph mapping.

### 10.5 Evidence-Backed Audit Artifact

Security Wiki acts as an audit artifact and is exportable. It is stored and indexed. However, it is Markdown and not structured enough for consistent thesis evaluation. Missing: validated schema, evidence refs by chunk ID, and stable export formatting.

### 10.6 Human-in-the-Loop Review

Manual verdict buttons and model comparison scoring exist. They should be used for evaluation/reporting only. Add richer comments, reviewer identity if needed for experiments, and summary statistics in export. Do not add self-training.

## 11. Current Testing Status

Automated backend tests exist in [backend/tests/test_current_pipeline.py](../backend/tests/test_current_pipeline.py).

Current tests cover:

- DB schema initialization.
- ZIP safe extraction normal case.
- ZIP path traversal rejection.
- Java parser method chunk creation.
- Security detection keywords.
- No-evidence chat safe refusal.
- Evidence card integrity.
- Wiki generation storage.
- Wiki indexing metadata.
- Manual feedback storage.
- Model comparison shared evidence package.

Run tests:

```powershell
cd backend
python -m pytest
```

What is missing:

- Frontend tests.
- Full integration tests with real local Chroma and a small repo fixture.
- Provider health endpoint tests.
- Structured JSON validation tests for valid/invalid evidence refs.
- Large repo guardrail tests.
- Export content tests.

Proposed test plan:

### Backend tests

- repo/ZIP import service,
- file filtering includes supported extensions,
- ignored folders are ignored,
- parser extracts Java/Python/Go functions with line ranges,
- security tag detection,
- vector indexing,
- evidence retrieval,
- no-evidence chat behavior,
- wiki generation storage,
- wiki indexing,
- model provider mock,
- export generation.

### Frontend tests

- project import form,
- discovery result rendering,
- selected module behavior,
- chat evidence cards,
- file open/highlight behavior,
- model selector,
- verification buttons.

### Integration tests

- import small test repo,
- run discovery,
- generate wiki,
- ask chat question,
- verify evidence card,
- export report.

## 12. What Is Missing / Next Work

1. Critical — Add Python/FastAPI support
   - Why: current `.py` files are not indexed, blocking Python RBAC case studies.
   - Files likely affected: [backend/app/services/files.py](../backend/app/services/files.py), [backend/app/services/parser.py](../backend/app/services/parser.py), tests.
   - Difficulty: medium.
   - Risk if not done: thesis cannot honestly claim Python/FastAPI support.

2. Critical — Implement tree-sitter or better parser
   - Why: regex chunks are fragile.
   - Files: [backend/app/services/parser.py](../backend/app/services/parser.py), tests.
   - Difficulty: high.
   - Risk: line ranges and method names may be unreliable.

3. High — Improve Security Wiki schema
   - Why: Markdown-only wiki is inconsistent.
   - Files: [backend/app/services/audit_service.py](../backend/app/services/audit_service.py), [backend/app/db/schemas.py](../backend/app/db/schemas.py).
   - Difficulty: medium.
   - Risk: weak evaluation artifact.

4. High — Add structured wiki evidence refs
   - Why: wiki should reference retrieved chunk IDs.
   - Files: audit service, vector index, export.
   - Difficulty: medium.
   - Risk: wiki claims may be hard to verify.

5. High — Better large repo controls
   - Why: full AOSP/large repos are risky.
   - Files: project service, import UI, DB.
   - Difficulty: high.
   - Risk: slow/failing demos.

6. Medium — Better Android case-study import
   - Why: Android support is currently link-based.
   - Files: project service, ProjectsPage.
   - Difficulty: medium.
   - Risk: Android thesis examples are hard to reproduce.

7. Medium — More tests
   - Why: current backend tests are useful but incomplete.
   - Files: `backend/tests`, frontend test setup.
   - Difficulty: medium.
   - Risk: regressions during thesis demo.

8. Medium — Export polish
   - Why: supervisor-facing reports need better formatting.
   - Files: [backend/app/services/audit_service.py](../backend/app/services/audit_service.py).
   - Difficulty: medium.
   - Risk: current report looks MVP-level.

9. Low — Model comparison dashboard
   - Why: scoring exists but presentation is basic.
   - Files: ProjectWorkspace, styles.
   - Difficulty: medium.
   - Risk: evaluation less clear.

10. Low — Better timing logs
    - Why: useful for explaining slow runs.
    - Files: services.
    - Difficulty: low.
    - Risk: harder debugging.

## 13. Database and Folder Explanation for Developer

Where is my SQLite DB file?

- Default: `./storage/security_codewiki.db`
- If backend is started from `backend`, this becomes `backend/storage/security_codewiki.db`.

How can I open it?

- Use DB Browser for SQLite, SQLite CLI, or a Python/sqlite script.
- Example path: `backend/storage/security_codewiki.db`.

What exactly is stored in it?

- Project metadata.
- Indexed files.
- Code chunks and line ranges.
- Wiki pages.
- Chat sessions/messages.
- Evidence JSON.
- Structured validation status.
- Model comparison results and scores.
- Manual verifications.

How long does data stay there?

- Until the SQLite file or rows are deleted. There is no automatic expiry.

How do I reset/delete a project?

- There is no delete project UI/route yet.
- Manual reset means deleting DB rows and project folders carefully, or deleting local storage DB/folders for a full reset.

Where are cloned repos stored?

- `PROJECT_STORAGE_PATH`, default `./storage/projects/{project_id}/repo`.

Where is Chroma stored?

- `CHROMA_DB_PATH`, default `./storage/chroma`.

Why are chroma/projects/exports folders sometimes empty?

- No import means no projects.
- No indexing means no Chroma vectors.
- Exports are streamed, not saved to disk.

Why might there be both `storage/` and `backend/storage/`?

- Relative paths are based on the process working directory.
- Running backend from repo root vs `backend` changes where `./storage` resolves.

How should I configure absolute storage paths?

Set absolute paths in `backend/.env`, for example:

```env
PROJECT_STORAGE_PATH=C:/Users/shahe/Downloads/Thesis/SecurityWiki/backend/storage/projects
CHROMA_DB_PATH=C:/Users/shahe/Downloads/Thesis/SecurityWiki/backend/storage/chroma
SQLITE_DB_PATH=C:/Users/shahe/Downloads/Thesis/SecurityWiki/backend/storage/security_codewiki.db
EXPORT_STORAGE_PATH=C:/Users/shahe/Downloads/Thesis/SecurityWiki/backend/storage/exports
```

## 14. Current User Guide

1. Start backend:

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

2. Start frontend:

```powershell
cd frontend
npm install
npm run dev
```

3. Open frontend:

```text
http://127.0.0.1:5173
```

4. Login with `.env` superuser credentials.

5. Create project:

- choose GitHub, ZIP, or Android;
- provide repo URL, ZIP file, or Android source link;
- optionally provide security goal.

6. Wait for status `indexed`.

7. Browse files in the left file tree.

8. Run discovery with an audit goal, for example:

```text
role based access control endpoints
```

9. Select a candidate file/module.

10. Generate Security Wiki.

11. Ask chat questions.

12. Click evidence cards to open and highlight source.

13. Compare models if providers are configured.

14. Save manual feedback/verdicts.

15. Export Markdown, JSON, or CSV.

Example questions:

Spring Boot / Java RBAC repo:

- "Which endpoints require ADMIN role?"
- "Where are requestMatchers and hasRole configured?"
- "Build an access-control matrix for these protected endpoints."

FastAPI / Python RBAC repo:

- "Where are role checks enforced?"
- "Which dependencies protect admin routes?"
- Note: current MVP does not index `.py` files yet, so this will not work properly until Python support is added.

Selected Android AccountManagerService source:

- "Where does AccountManagerService check caller permissions?"
- "Which helper methods validate the calling UID?"
- "What evidence shows token access is protected?"

## 15. Final Recommendations

Overall status:

Security CodeWiki is a solid local MVP and is on track. It imports code, indexes chunks, detects security candidates, generates a selected-target Security Wiki, performs evidence-first chat, opens source evidence in Monaco, supports multiple LLM providers, stores manual audit feedback, and exports reports.

Top 5 fixes required next:

1. Add Python/FastAPI file support and chunking.
2. Replace regex parser with tree-sitter or a stronger parser for Java/Go/Python.
3. Make Security Wiki output structured and validated with evidence refs.
4. Improve large-repo/case-study workflow with selected subfolder import and job progress.
5. Add frontend/integration tests and polish export for thesis presentation.

What should not be built yet:

- Full multi-user auth.
- Self-training from feedback.
- Full Android AOSP import.
- VS Code extension.
- Exploit generation.
- Full static call graph engine unless narrowed to one thesis case study.

How to present current MVP to a supervisor:

Present it as an evidence-backed local audit assistant prototype. Emphasize that it already demonstrates the thesis workflow: import source, discover candidate security files, generate a Security Wiki, ask evidence-backed questions, open exact code evidence, compare models, and export results. Be honest that parsing and security reasoning are still heuristic and that the next research-quality step is stronger parsing, structured wiki output, and more rigorous evaluation.
