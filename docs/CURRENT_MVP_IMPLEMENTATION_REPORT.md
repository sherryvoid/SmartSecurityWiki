# Security CodeWiki — Current MVP Implementation Report

## 1. Executive Summary

Security CodeWiki is currently a working local MVP that can import source code, index supported files into line-aware chunks, detect candidate security-relevant files, generate a Security Wiki for a selected candidate, retrieve source-code evidence and wiki context, ask an LLM for an evidence-backed answer, show evidence cards, compare model outputs, store manual audit feedback, and export project data.

It is a combination of a repo browser, raw-code RAG chatbot, Security Wiki assistant, and early model comparison tool. Its strongest parts are the end-to-end local workflow, backend-generated evidence cards, source line ranges, Chroma indexing, and model/provider flexibility. Its weakest parts are parser precision, conceptual module modeling, structured wiki validation, large-repository handling, and frontend test coverage.

The MVP still follows the thesis direction. It has not drifted into a generic chatbot because chat refuses to answer without source-code evidence and evidence cards are generated from backend chunks, not invented by the model. However, several thesis-grade pieces remain partial: the parser is heuristic instead of tree-sitter-based, discovery returns candidate files rather than real modules, and model comparison/evaluation is useful but still basic.

## 2. Current Project Structure

Clean project tree, excluding caches, virtual environments, Chroma internals, `node_modules`, and large generated files:

```text
SecurityWiki/
  .gitignore
  .vscode/
    extensions.json
  README.md
  backend/
    .env
    .env.example
    pytest.ini
    requirements.txt
    app/
      main.py
      api/
        routes/
          auth.py
          health.py
          models.py
          projects.py
      core/
        config.py
        security.py
      db/
        database.py
        models.py
        schemas.py
      services/
        audit_service.py
        files.py
        llm.py
        model_health.py
        parser.py
        project_service.py
        security_detection.py
        vector_index.py
      utils/
    storage/
      security_codewiki.db
      projects/
      chroma/
      exports/
    tests/
      conftest.py
      test_current_pipeline.py
  docs/
    current-mvp-status-and-gap-analysis.md
    latest-security-codewiki-mvp-completion-roadmap.md
    CURRENT_MVP_IMPLEMENTATION_REPORT.md
  frontend/
    package.json
    tsconfig.json
    vite.config.ts
    src/
      App.tsx
      main.tsx
      styles.css
      pages/
        LoginPage.tsx
        ProjectsPage.tsx
        ProjectWorkspace.tsx
      services/
        api.ts
      types/
        index.ts
  storage/
    projects/
    chroma/
    exports/
```

Important folders:

- `backend/app`: FastAPI application code.
- `backend/app/api/routes`: HTTP API routes.
- `backend/app/services`: import, parsing, detection, vector, LLM, audit, and model health logic.
- `backend/app/db`: SQLite schema, migrations, and Pydantic request/response schemas.
- `backend/tests`: pytest backend tests.
- `frontend/src`: React/Vite frontend.
- `docs`: project reports and roadmap files.
- `backend/storage`: default runtime storage when backend is started from `backend`.
- `storage`: possible root-level storage when commands are run from repo root or absolute paths are not configured.

## 3. Current Tech Stack

Frontend:

- Framework: React `18.3.1`.
- Language: TypeScript.
- Build/dev server: Vite `6.0.5`.
- Routing: `react-router-dom` `7.1.1`.
- API client: custom `fetch` wrapper in `frontend/src/services/api.ts`.
- Code viewer: Monaco via `@monaco-editor/react`.
- Styling: plain CSS in `frontend/src/styles.css`.
- Icons: `lucide-react`.
- Tests: no frontend test framework is currently configured in `frontend/package.json`.

Backend:

- Framework: FastAPI `0.115.6`.
- Language: Python.
- Server: Uvicorn.
- Database: SQLite using direct `sqlite3` access.
- Vector DB: ChromaDB `0.6.3`.
- Parser/chunker: regex and heuristic parsing in `backend/app/services/parser.py`.
- Tree-sitter: packages are listed in `backend/requirements.txt`, but current parser logic does not use tree-sitter.
- Embedding provider: sentence-transformers by default with hash fallback in `backend/app/services/vector_index.py`.
- Auth approach: one env-configured superuser with JWT bearer token.
- Test framework: pytest.

Local/Cloud AI:

- Ollama/Qwen: implemented in `backend/app/services/llm.py`; default configured as `qwen3.5:9b`.
- Gemini: implemented; default setting is `gemini-1.5-flash`, with `resolved_gemini_default_model` falling back to `gemini-2.5-flash` only if the configured env value is blank.
- OpenAI: implemented.
- DeepSeek: implemented.
- Health checks: `GET /api/models/health` implemented in `backend/app/api/routes/models.py` and `backend/app/services/model_health.py`.

## 4. Current User Flow

1. Login
   - UI: `frontend/src/pages/LoginPage.tsx`.
   - Route: `POST /api/auth/login`.
   - Service/core: `backend/app/core/security.py`.
   - Required fields: username and password.
   - Limitation: no users table, no registration, no roles; only env superuser.

2. Create/import project
   - UI: `frontend/src/pages/ProjectsPage.tsx`.
   - GitHub/Android route: `POST /api/projects`.
   - ZIP route: `POST /api/projects/zip`.
   - Service: `backend/app/services/project_service.py`.
   - Required fields: project name and source type; GitHub/Android require repo URL when creating a real import; ZIP requires file.
   - Optional fields: security goal and subfolder path.
   - Limitation: Android is selected-source/case-study oriented, not full AOSP.

3. Wait for indexing
   - UI: project status/progress in `ProjectWorkspace`.
   - Route: `GET /api/projects/{project_id}/status`.
   - Service: `project_status`.
   - Limitation: clone progress percentage is unavailable; progress starts becoming meaningful during scanning/indexing.

4. Browse files
   - UI: file tree and Monaco code viewer in `ProjectWorkspace`.
   - Routes: `GET /api/projects/{project_id}/files/tree`, `GET /api/projects/{project_id}/files/content`.
   - Service: `file_tree`, `file_content`.
   - Limitation: no advanced tree pagination for very large repos.

5. Run discovery
   - UI: discovery panel/cards in `ProjectWorkspace`.
   - Route: `POST /api/projects/{project_id}/discover-security-modules`.
   - Service: `discover_security_modules`.
   - Optional input: audit/security goal text.
   - Limitation: returns candidate files/modules, not true architecture modules.

6. Select candidate file/module
   - UI: discovery card `Select for Analysis`.
   - Backend model: selected module is currently a file path.
   - Effect: scopes wiki generation, chat, and comparison when supplied.
   - Limitation: no modules table/entity.

7. Generate Security Wiki
   - UI: wiki panel in `ProjectWorkspace`.
   - Route: `POST /api/projects/{project_id}/wiki/generate`.
   - Service: `generate_wiki`.
   - Required: selected candidate/module path.
   - Limitation: Markdown prompt output, not validated structured wiki JSON.

8. Ask chat question
   - UI: chat panel in `ProjectWorkspace`.
   - Route: `POST /api/projects/{project_id}/chat`.
   - Service: `chat`.
   - Required: question; provider/model optional.
   - Limitation: LLM prose can still be imperfect, but evidence cards are backend-controlled.

9. Open evidence card
   - UI: evidence cards in chat response.
   - Route used indirectly: file content route.
   - Behavior: opens file in Monaco, scrolls/highlights range, and highlights critical lines.
   - Limitation: highlighting depends on Monaco decorations and available file content.

10. Compare models
    - UI: comparison panel in `ProjectWorkspace`.
    - Route: `POST /api/projects/{project_id}/compare-models`.
    - Service: `compare_models`.
    - Limitation: useful but still a basic evaluation view.

11. Give manual audit feedback
    - UI: verification buttons and comparison scoring controls.
    - Routes: `POST /api/projects/{project_id}/verify`, `PATCH /api/projects/{project_id}/evaluations/{evaluation_id}`.
    - Services: `verify`, `score_evaluation`.
    - Important: feedback is manual audit/evaluation data only. It does not self-train models.

12. Export report
    - UI: export buttons.
    - Route: `GET /api/projects/{project_id}/export/{format}`.
    - Service: `export_project`.
    - Formats: Markdown, JSON, CSV.
    - Limitation: exported report is functional but not yet a polished thesis artifact.

## 5. Current Backend Architecture

- `backend/app/main.py`
  - Creates the FastAPI app.
  - Initializes storage and SQLite schema on startup.
  - Adds CORS middleware.
  - Includes auth, health, models, and project routers.

- `backend/app/core/config.py`
  - Defines `Settings`.
  - Reads `.env`.
  - Configures storage paths, SQLite path, Chroma path, CORS, LLM providers, embedding settings, and large repo guardrails.
  - Important values: `project_storage_path`, `chroma_db_path`, `sqlite_db_path`, `ollama_timeout_seconds`, `cloud_llm_timeout_seconds`, `max_total_files_to_index`, `max_total_chunks_to_index`, `git_clone_timeout_seconds`.
  - Has `resolved_gemini_default_model`.

- `backend/app/core/security.py`
  - Implements JWT creation and bearer-token validation.
  - Uses env superuser credentials.
  - No persistent user account system.

- `backend/app/db/database.py`
  - Owns SQLite connection, schema creation, and safe column migration.
  - Uses WAL and busy timeout.
  - Creates `projects`, `files`, `code_chunks`, `wiki_pages`, `chat_sessions`, `chat_messages`, `evaluations`, and `verifications`.

- `backend/app/db/schemas.py`
  - Pydantic request/response schemas.
  - Includes project creation, chat, compare, wiki generation, verification, and evaluation score schemas.

- `backend/app/api/routes/auth.py`
  - `POST /api/auth/login`.
  - Validates env superuser and returns JWT.

- `backend/app/api/routes/health.py`
  - `GET /api/health`.
  - Basic backend health endpoint.

- `backend/app/api/routes/models.py`
  - `GET /api/models/health`.
  - Protected by JWT.

- `backend/app/api/routes/projects.py`
  - Main project router.
  - Handles project creation, ZIP upload, Android case studies, status, rebuild index, file tree/content, discovery, wiki, chat, comparison, verification, evaluation scoring, and export.

- `backend/app/services/files.py`
  - File inclusion/ignore rules.
  - Language detection.
  - Safe text reading and file walking.

- `backend/app/services/parser.py`
  - Regex/heuristic chunk creation.
  - Supports Java/C/C++-style symbols, Go functions, Python classes/functions/async functions, JavaScript/TypeScript functions/classes/arrows/routes/decorators, Markdown headings, and fallback line-range chunks.

- `backend/app/services/security_detection.py`
  - Keyword/tag-based security detection.
  - Supports Java/Android/Spring, Python/FastAPI/Django, JavaScript/TypeScript/Express/Nest, config/policy files, and generic auth terms.

- `backend/app/services/vector_index.py`
  - Chroma initialization and indexing.
  - Embedding abstraction with sentence-transformers and hash fallback.
  - Indexes code chunks and wiki chunks.
  - Provides vector query and embedding status.

- `backend/app/services/llm.py`
  - Provider abstraction for Ollama, OpenAI, Gemini, and DeepSeek.
  - Handles provider/model selection and timeouts.
  - Returns safe provider responses when cloud API keys are missing.

- `backend/app/services/model_health.py`
  - Provider readiness checks.
  - Checks Ollama `/api/tags`.
  - Reports whether cloud API keys/default models are configured.
  - Includes embedding provider status.

- `backend/app/services/project_service.py`
  - Project creation, clone, ZIP extraction, safe subfolder handling, indexing, file tree/content, discovery, evidence retrieval, wiki context retrieval, critical-line detection, and project status.

- `backend/app/services/audit_service.py`
  - Security Wiki generation.
  - Evidence-first chat.
  - Structured chat JSON parsing and validation.
  - Model comparison persistence.
  - Manual verification/scoring.
  - Export generation.

- `backend/tests`
  - pytest tests covering schema, ZIP safety, parser, detection, evidence, wiki, comparison, Gemini config, subfolder import, and no-evidence chat behavior.

## 6. Current Frontend Architecture

- `frontend/src/App.tsx`
  - Main React router.
  - Provides protected project routes based on local token presence.
  - Provides top navigation and logout.

- `frontend/src/pages/LoginPage.tsx`
  - Login form.
  - Calls API login and stores JWT in `localStorage`.

- `frontend/src/pages/ProjectsPage.tsx`
  - Lists existing projects.
  - Creates GitHub, ZIP, or Android-source projects.
  - Includes optional subfolder path and security goal.
  - Shows large repo warning.

- `frontend/src/pages/ProjectWorkspace.tsx`
  - Main workspace.
  - Shows project status/progress, file tree, Monaco code viewer, discovery, selected candidate, wiki, chat, evidence cards, model selector/status, comparison, manual feedback, and exports.

- `frontend/src/services/api.ts`
  - Central API client.
  - Adds bearer token.
  - Handles 401 by clearing token and redirecting to login.

- `frontend/src/types/index.ts`
  - TypeScript interfaces for projects, files, candidates, evidence, chat, wiki, model health, etc.

- `frontend/src/styles.css`
  - Entire UI styling.
  - Includes layout, cards, progress, code/evidence states, and Monaco highlight styles.

## 7. Repository Import and Storage Flow

GitHub clone:

- Implemented in `backend/app/services/project_service.py`.
- Uses `git clone --depth 1`.
- Has timeout through `git_clone_timeout_seconds` in `Settings`.
- If clone fails, retry logic attempts safer Git config/proxy behavior.
- If clone still fails, project status becomes `failed` with a status message.

ZIP upload:

- Implemented by `create_project_from_zip`.
- Extracts to a project directory.
- Uses `_safe_extract_zip` to reject path traversal entries.
- ZIP indexing runs after extraction in the request flow, not as a separate durable job queue.

Android mode:

- Supported as selected case-study/source-link import through configured Android case study metadata and GitHub-like import.
- Full Android AOSP import is not supported.
- Practical path is selected source/case-study packages or selected subfolder indexing.

Subfolder import:

- Supported by optional `subfolder_path`.
- Current practical implementation is shallow clone, then index only the selected subfolder.
- It is not true Git sparse checkout.
- Path traversal is guarded through normalization/safe path checks.
- Missing subfolder marks the project failed.

Progress/status:

- Stored in `projects` table.
- Fields include `status`, `status_message`, `progress_percent`, file/chunk counters, and `current_file`.
- Stages include fetching, fetched, scanning, indexing files, embedding, indexing chunks, indexed, and failed.
- Clone percentage is not available; frontend should show indeterminate clone state.

Where to check cloned repos:

- Usually `backend/storage/projects/<project_id>` if backend is started from `backend`.
- If launched from repo root with relative paths, storage may appear under root `storage/`.
- Absolute paths in `.env` avoid confusion.

Why storage folders may be empty:

- `storage/projects` is empty if no project has been imported under that working directory.
- `storage/chroma` is empty if no Chroma collection has been created under that working directory.
- `storage/exports` may be empty because exports are streamed directly from the API.
- Both root `storage/` and `backend/storage/` can exist when backend commands are run from different working directories with relative paths.

## 8. File Type and Language Support

| Tech / Language | Extensions | Currently Indexed? | Parser Support | Security Detection Support | Notes / Limitations |
|---|---:|---|---|---|---|
| Java | `.java` | Yes | Heuristic class/method regex | Yes | Good MVP support, not full AST. |
| Spring Boot/Spring Security | `.java`, `.xml`, `.yaml`, `.yml` | Yes | Java regex and config fallback | Yes | Detects roles, matchers, annotations, filters. |
| Android Java | `.java` | Yes | Java regex | Partial/Yes | Detects Binder/permission patterns, no full Android framework model. |
| AIDL | `.aidl` | Yes | Fallback chunks | Partial | Indexed and searchable, no dedicated AIDL parser. |
| C | `.c`, `.h` | Yes | C-style regex/fallback | Partial | No AST/call graph. |
| C++ | `.cpp`, `.h` | Yes | C-style regex/fallback | Partial | No AST/call graph. |
| SELinux `.te` | `.te` | Yes | Fallback chunks | Partial | Policy file detection, no SELinux policy engine. |
| Go | `.go` | Yes | Function regex | Partial/Yes | Useful for Kubernetes-style code, no full AST. |
| Kubernetes Go | `.go`, `.yaml`, `.yml` | Yes | Go regex/config fallback | Partial | No Kubernetes-specific semantic analysis. |
| Python | `.py` | Yes | Class, def, async def indentation heuristic | Yes | Recently supported; no Python AST module yet. |
| FastAPI | `.py` | Yes | Python heuristic | Yes | Detects Depends, Security, JWT, HTTP 401/403, routes. |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | Yes | Functions/classes/arrows/routes/decorators | Yes | Heuristic only; complex syntax may be missed. |
| TypeScript | `.ts`, `.tsx` | Yes | Functions/classes/arrows/routes/decorators | Yes | No TypeScript compiler/AST. |
| Node/Express | JS/TS extensions | Yes | Route handler regex where practical | Yes | Common `app.get`, `router.post`, middleware terms detected. |
| JSON | `.json` | Yes | Fallback chunks | Config-file detection | Good for configs, no schema-specific parser. |
| YAML | `.yaml`, `.yml` | Yes | Fallback chunks | Config-file detection | Useful for policy/config references. |
| XML | `.xml` | Yes | Fallback chunks | Config-file detection | Useful for Android/Spring configs. |
| Markdown/TXT | `.md`, `.txt` | Yes | Markdown heading chunks or fallback | Limited | Helpful for README/context, not source proof. |

## 9. Parsing and Chunking

Parsing is implemented in `backend/app/services/parser.py`. It is regex/heuristic based. Tree-sitter dependencies exist in `requirements.txt`, but the code does not currently call tree-sitter.

Chunking behavior:

- Java/C/C++ style files use regexes for class and method/function-like declarations.
- Go files use a Go function regex.
- Python files use regexes for `class`, `def`, and `async def`, with indentation-based end-line detection.
- JavaScript/TypeScript files use regexes for function declarations, exported functions, classes, arrow assignments, Express-style routes, and Nest-style decorators.
- Markdown files can be chunked by headings.
- Other supported files use fallback line-range chunks, currently around 80 lines.

Line numbers:

- Start and end lines are generated by the backend during parsing.
- Stored in `code_chunks`.
- Evidence cards reuse these backend line ranges.

Reliability:

- Good enough for an MVP and small demos.
- Not thesis-perfect for complex syntax, multiline declarations, nested classes, decorators, lambdas, chained route definitions, macros, or generated code.
- A stronger future version should use tree-sitter or language-native ASTs.

## 10. Security Candidate Detection and Discovery

Security detection is implemented in `backend/app/services/security_detection.py`.

It uses keyword/pattern matching and maps matches to tags such as:

- `potential_access_check`
- `potential_policy_file`
- `potential_entry_point`
- `potential_helper`
- `potential_config_file`

Supported patterns include:

- Java/Spring/Android permission, role, matcher, Binder, exception, and policy terms.
- Python/FastAPI/Django terms such as `Depends`, `Security`, `OAuth2PasswordBearer`, `get_current_user`, `require_role`, `HTTPException`, `401`, `403`, permissions, and decorators.
- JS/TS/Node/Nest terms such as `jwt`, `passport`, `requireAuth`, `UseGuards`, `AuthGuard`, `RolesGuard`, `ForbiddenException`, and Express route patterns.

Discovery is implemented in `discover_security_modules` in `project_service.py` and exposed through `POST /api/projects/{project_id}/discover-security-modules`.

Discovery does not use an LLM. It scores files based on existing chunk tags, matched symbols, query/security goal terms, and chunk counts. It returns candidate files/modules. The term “module” is conceptual in the UI; technically the selected module is currently a file path.

If the user types “role based access control endpoints,” discovery should favor files containing endpoint definitions, route/matcher terms, role checks, JWT/auth terms, or security tags. In a Spring Boot RBAC repo, likely candidates would include security configuration classes, controller files with protected endpoints, filters, and user/role authorization helpers.

## 11. RAG and Chroma Implementation

Chroma is implemented in `backend/app/services/vector_index.py` using `chromadb.PersistentClient(path=settings.chroma_db_path)`.

Embedding behavior:

- Default provider setting is `sentence-transformers`.
- Default model is `BAAI/bge-small-en-v1.5`.
- The SentenceTransformer loader uses `local_files_only=True`, so the model must already be available locally.
- If sentence-transformers or the local model is unavailable, the system falls back to hash embeddings.
- `GET /api/models/health` includes embedding status through `embedding_status()`.

Code chunk metadata includes:

- `source_type: code`
- `chunk_id`
- `project_id`
- `file_path`
- `symbol_name`
- `start_line`
- `end_line`
- `language`
- `security_tags`

Wiki chunk metadata includes:

- `source_type: wiki`
- `project_id`
- `wiki_page_id`
- `module_id`
- `title`
- `section_title`
- `chunk_index`
- `created_at`

Retrieval:

- `vector_index.query` performs Chroma vector query.
- `project_service.retrieve_evidence` combines vector hits with keyword/security scoring over stored chunks.
- This is hybrid-ish in practice, but not a full formal hybrid retriever with BM25/symbol graph/call graph.
- Default evidence count is about 8 chunks.
- If selected module/file path is supplied, retrieval is scoped to that file.
- Rebuild index exists through `POST /api/projects/{project_id}/rebuild-index`.

## 12. Security Wiki Implementation

The Security Wiki is this project’s own generated audit documentation. It is not Google Code Wiki, and Google Code Wiki is not used anywhere in the codebase.

Wiki generation:

- Route: `POST /api/projects/{project_id}/wiki/generate`.
- Service: `generate_wiki` in `audit_service.py`.
- Trigger: manual user action.
- Scope: selected candidate file/module path, not whole project by default.
- Evidence: retrieved backend code evidence for the selected candidate and security goal.
- Output: Markdown generated by the selected LLM using required section headings.
- Storage: row in `wiki_pages`.
- RAG: generated wiki Markdown is split into chunks and indexed into Chroma with `source_type="wiki"`.
- Chat: later chat retrieves wiki context separately from raw source-code evidence.

Limitations:

- Wiki is Markdown prompt output, not a validated structured JSON `SecurityWiki` schema.
- Evidence references are not fully schema-validated against retrieved chunks in the same way chat evidence refs are.
- Quality depends on retrieved evidence and the selected LLM.

## 13. Chat / LLM Answer Flow

Chat flow is implemented in `chat` in `backend/app/services/audit_service.py`.

When the user asks a question:

1. Backend retrieves raw source-code evidence chunks.
2. Backend retrieves wiki context if generated wiki exists.
3. If no raw code evidence exists, it returns: `Not verified from the available source-code evidence.`
4. If evidence exists, backend builds a prompt containing separate source evidence and wiki context.
5. Backend asks the selected LLM to return structured JSON.
6. Backend parses and validates the JSON against known evidence IDs.
7. Backend stores raw model response, parsed JSON if valid, and validation status.
8. Backend returns evidence cards generated from retrieved backend chunks, not from model-invented file paths.

Model choice affects generation, not indexing/retrieval.

Structured chat validation exists for chat answers:

- `answer`
- `confidence`
- `access_control_summary`
- `evidence_refs`
- `helper_chain`
- `limitations`
- `needs_review`

Invalid model responses fall back to raw text with a validation warning/status. Evidence cards remain backend-generated. The prose can still be imperfect, but file paths/line ranges/snippets shown as cards are controlled by backend evidence.

## 14. Evidence Cards and Code Viewer

Evidence cards are built from stored `code_chunks` and retrieval results.

Each evidence card can contain:

- `chunk_id`
- file path
- symbol/function/class/block name
- start line
- end line
- language
- security tags
- confidence
- code snippet
- critical lines
- reason/why relevant

Critical-line detection is implemented in `project_service.py` using access-control keywords such as permission checks, role checks, authority checks, `SecurityException`, `Forbidden`, `Unauthorized`, `getCallingUid`, `requestMatchers`, `permitAll`, and related terms.

Frontend behavior:

- Clicking an evidence card opens the file in Monaco.
- The viewer scrolls to the evidence range.
- The range is highlighted.
- Critical lines are highlighted more strongly where possible.

Limitations:

- Highlighting depends on Monaco decoration behavior.
- No semantic call-chain highlighting.
- Evidence snippets are chunk-level, not guaranteed minimal critical statement extraction.

## 15. LLM Providers and Model Health

LLM providers are implemented in `backend/app/services/llm.py`.

Ollama/Qwen:

- Configured with `OLLAMA_BASE_URL`.
- Default model is `OLLAMA_DEFAULT_MODEL`, currently `qwen3.5:9b` in settings unless `.env` overrides it.
- Ollama calls use `/api/chat`.
- Timeout is `OLLAMA_TIMEOUT_SECONDS`, default `300`.

Gemini:

- Uses `GEMINI_API_KEY`.
- Default setting is `gemini-1.5-flash`.
- If `GEMINI_DEFAULT_MODEL` is explicitly blank, `resolved_gemini_default_model` returns `gemini-2.5-flash`.
- This means a free-tier user can leave Gemini model blank, but if the default config value is not overridden, the resolved value remains `gemini-1.5-flash`.

OpenAI:

- Uses `OPENAI_API_KEY`.
- Default model is `gpt-4o-mini`.

DeepSeek:

- Uses `DEEPSEEK_API_KEY`.
- Default model is `deepseek-chat`.

Health checks:

- Route: `GET /api/models/health`.
- Ollama: checks reachability and available tags; reports whether default model exists.
- Cloud providers: reports whether API key and default/resolved model are configured.
- Embeddings: reports active provider, model, semantic/fallback status, and warning.
- API keys are not exposed to the frontend.

Known issues:

- Ollama cold start can make first requests slow.
- If sentence-transformers model is not cached locally, embeddings fall back to hash.
- Provider readiness does not guarantee generation will succeed under rate limits or runtime model errors.

## 16. Model Comparison and Manual Evaluation

Model comparison exists.

Implemented behavior:

- Route: `POST /api/projects/{project_id}/compare-models`.
- Service: `compare_models`.
- Retrieves evidence once.
- Retrieves wiki context once.
- Sends the same evidence package to each selected provider/model.
- Stores each result in `evaluations`.
- Stores provider, model, answer text, parsed JSON where available, evidence JSON, wiki context JSON, validation status, latency, and score fields.

Manual scoring fields exist:

- `correct_file_path`
- `correct_code_block`
- `explanation_quality`
- `completeness`
- `hallucination_flag`
- `usefulness`
- `evaluator_comment`

Limitations:

- Cost is not meaningfully estimated; `estimated_cost` is present but not a complete billing calculation.
- UI is functional but not a polished research dashboard.
- No automatic metric computation beyond stored validation and manual scoring.

Manual evaluation/feedback is not self-training.

## 17. Export / Report Generation

Export is implemented in `export_project` in `backend/app/services/audit_service.py`.

Formats:

- Markdown
- JSON
- CSV

Export route:

- `GET /api/projects/{project_id}/export/{export_format}`

Included data:

- Project metadata.
- Wiki pages.
- Chat questions/answers.
- Evidence JSON where stored.
- Model comparison/evaluation results.
- Manual verification feedback.
- Limitations where available.

Storage behavior:

- Exports are streamed as API responses.
- `storage/exports` may be empty because files are not necessarily saved to disk.

Missing for thesis-quality export:

- Better formatting.
- More explicit access-control matrix sections.
- More structured requirement-to-code trace sections.
- Cleaner comparison/evaluation tables.
- Stronger linkage between wiki evidence refs and code chunks.

## 18. Database Map

SQLite DB path:

- Configured by `SQLITE_DB_PATH`.
- Default: `./storage/security_codewiki.db`.
- Actual location depends on backend working directory.
- Common location: `backend/storage/security_codewiki.db`.

How to open:

- Use VS Code SQLite Viewer/SQLite extension, DB Browser for SQLite, or `sqlite3`.
- Recommended VS Code extensions are listed in `.vscode/extensions.json`.

Tables:

- `projects`
  - Stores project metadata, source type, repo URL, local path, subfolder path, status, progress, security goal, timestamps.

- `files`
  - Stores indexed file paths, language, size, security tags, and relevance status.
  - Linked to `projects`.

- `code_chunks`
  - Stores parser-generated chunks, symbol names, class names, line ranges, code, tags, and embedding IDs.
  - Linked to `projects` and `files`.

- `wiki_pages`
  - Stores generated Security Wiki Markdown, selected module path, provider/model, evidence JSON, and timestamps.
  - Linked to `projects`.

- `chat_sessions`
  - Stores chat session metadata.
  - Present for session organization, though current UI behavior is simple.

- `chat_messages`
  - Stores user questions, assistant answers, evidence JSON, wiki context JSON, provider/model, latency, raw model response, parsed JSON, and validation status.
  - Linked to `projects` and optionally `chat_sessions`.

- `evaluations`
  - Stores model comparison outputs and manual scoring fields.
  - Linked to `projects`.

- `verifications`
  - Stores manual audit verdicts/comments for answers or artifacts.
  - Linked to `projects`.

There is no real `users` table. There is no real `modules` table. Manual feedback is stored in `verifications` and `evaluations` and is not used for self-training.

Data persists until the SQLite DB, project storage, or Chroma storage is deleted. To reset safely, stop the backend first, then remove the relevant project rows/storage or delete the local storage folders for a full reset.

## 19. Testing Status

Backend tests exist in `backend/tests`.

Known test files:

- `backend/tests/conftest.py`
  - Test fixtures and isolated temporary environment.

- `backend/tests/test_current_pipeline.py`
  - Tests DB schema creation.
  - Tests ZIP safe extraction.
  - Tests parser/chunker behavior.
  - Tests security detection.
  - Tests file filtering.
  - Tests Python and JS parsing.
  - Tests no-evidence chat behavior.
  - Tests evidence card integrity.
  - Tests wiki storage and wiki indexing.
  - Tests manual verification.
  - Tests model comparison shared evidence.
  - Tests Gemini default behavior.
  - Tests selected subfolder import and missing subfolder failure.

How to run backend tests:

```powershell
cd backend
python -m pytest
```

Frontend tests:

- No frontend test framework is configured.

Integration tests:

- No full browser/end-to-end integration suite is configured.

Recommended test checklist:

- Repo import.
- ZIP import.
- File filtering.
- Python indexing.
- JS/TS indexing.
- Parser line ranges.
- Security detection.
- Chroma indexing.
- Wiki generation.
- Chat no-evidence behavior.
- Evidence card integrity.
- Model health.
- Gemini default model behavior.
- Export.

## 20. Current Known Bugs / Issues

- Python/FastAPI indexing was previously missing but is now implemented through `.py` inclusion and Python parser/detection support.
- JS/TS indexing was previously missing but is now implemented for common JS/TS extensions.
- Gemini free API behavior is partially fixed: blank env value resolves to `gemini-2.5-flash`, but the default config value is still `gemini-1.5-flash` unless `.env` overrides it.
- Chroma telemetry noise may appear in logs and is not necessarily a functional failure.
- Slow first Ollama/Qwen request is expected when the model cold-starts.
- Relative storage path confusion remains possible if backend is started from different directories.
- Subfolder import is supported as shallow clone plus index-only-subfolder, not true sparse checkout.
- Clone/index percentage is partial; clone percentage itself is unavailable.
- Full Android AOSP is not supported.
- Parser is heuristic and not tree-sitter-based.
- Security Wiki is Markdown prompt output, not validated structured JSON.
- Discovery returns candidate files, not real modules.
- No frontend automated tests.

## 21. Side-by-Side: Original Thesis Plan vs Current MVP

| Original Thesis Plan Feature | Current Status | Current Implementation | What Is Wrong or Missing | Practical Impact | Recommended Next Action |
|---|---|---|---|---|---|
| Local MVP | Done | React + FastAPI local app | None major | Good demo foundation | Stabilize docs/tests |
| Superuser login from `.env` | Done | Env credentials + JWT | No user table | Acceptable for local MVP | Keep scope |
| Public GitHub repo import | Done | Shallow clone + background task | Network/git failures still possible | Works for small repos | Improve error messages |
| ZIP upload | Done | Safe extraction + indexing | Runs without durable job queue | Good MVP | Add progress for ZIP indexing |
| Android selected source/case-study import | Partial | Case-study/source-link style import | Not full Android source workflow | OK for thesis case studies | Curate sample packages |
| Full Android AOSP handling | Missing | Explicitly not supported | Too large for MVP | Avoids unrealistic scope | Do not build yet |
| Subfolder clone/import | Partial | Shallow clone, index selected subfolder | Not true sparse checkout | Practical enough | Add sparse checkout later |
| File browser | Done | File tree route + frontend | No huge-tree pagination | Works for small/medium repos | Add pagination later |
| Monaco/code viewer | Done | `@monaco-editor/react` | Limited by file content loading | Strong UX feature | Keep |
| Method/function chunks with line ranges | Done/Partial | Heuristic parser | Not AST-grade | Evidence ranges useful but imperfect | Add tree-sitter |
| Tree-sitter parser | Missing | Packages installed only | Not used | Thesis parser quality weaker | Implement AST parsing |
| Java support | Done/Partial | Regex parser/detection | Complex Java may be missed | Good demos | Add tree-sitter Java |
| Go support | Partial | Function regex | No AST/call graph | OK for simple Go | Add tree-sitter Go |
| Python/FastAPI support | Done/Partial | `.py`, parser, detection | Heuristic only | Useful for FastAPI repos | Add Python AST/tree-sitter |
| JavaScript/TypeScript support | Done/Partial | JS/TS extensions/parser/detection | Heuristic only | Useful for Node demos | Add TS parser later |
| C/C++ support | Partial | Extensions + C-style regex/fallback | No real C/C++ parser | Limited Android native support | Consider tree-sitter C/C++ |
| AIDL/XML/YAML/JSON/.te support | Partial | Indexed/fallback chunks | No domain parser | Searchable but shallow | Add targeted policy parsers |
| Security candidate detection | Done/Partial | Keyword/tag scoring | No dataflow/call graph | Useful heuristic discovery | Improve scoring |
| Discovery button/module selection | Partial | Candidate files as modules | No module entity | Conceptually weak | Keep wording clear |
| Security Wiki generation | Done | LLM Markdown from selected target evidence | Not fully structured | Useful artifact | Add JSON schema wiki |
| Security Wiki indexed into RAG | Done | `index_wiki_page` | Chunking is heading/line based | Chat can use wiki context | Improve metadata refs |
| Structured Security Wiki schema | Missing | Prompted Markdown only | No schema validation | Less thesis-rigorous | Implement structured wiki |
| Raw code RAG | Done | Chroma + chunks | Semantic depends on local model | Core feature works | Verify embeddings |
| Semantic embeddings | Partial | sentence-transformers with local-only loading | Falls back if model not cached | Could become hash retrieval | Document/install model |
| Evidence-first chat | Done | Retrieves source evidence before LLM | Retrieval can miss evidence | Core principle present | Improve retrieval |
| No evidence, no answer | Done | Safe refusal phrase | None major | Strong safety behavior | Preserve |
| Structured JSON chat answer | Done/Partial | Pydantic parsing/validation | Repair/fallback basic | Reduces hallucination risk | Strengthen validation |
| Evidence cards | Done | Backend chunks | Snippet can be broad | Strong thesis alignment | Refine critical snippets |
| Code viewer highlight | Done/Partial | Monaco decorations | No semantic graph highlight | Good auditor UX | Keep polishing |
| Local Qwen/Ollama | Done | Ollama provider | Needs local Ollama/model | Good local story | Health UX |
| Gemini | Done/Partial | Gemini provider | Default behavior nuance | Usable with key/model | Set docs clearly |
| OpenAI | Done | Provider implemented | Requires key | Works if configured | Keep |
| DeepSeek | Done | Provider implemented | Requires key | Works if configured | Keep |
| Model health checks | Done | `/api/models/health` | Runtime success not guaranteed | Helps UX | Expand details later |
| Model comparison | Partial/Done | Same evidence package, persisted | UI/basic scoring still limited | Useful evaluation feature | Polish dashboard |
| Manual audit feedback | Done | Verifications/evaluation scores | Not deeply surfaced | Good thesis evaluation basis | Include in reports |
| Export audit report | Partial | MD/JSON/CSV stream | Formatting not thesis-polished | Useful but rough | Improve report template |
| Large repo handling | Partial | limits, timeout, subfolder | No queue/cancel/sparse clone | Risk for huge repos | Add job system later |
| Progress loader/percentage | Partial | status/progress fields | Clone percent unavailable | Better but not full | Add stage logs |
| Tests | Partial/Good backend | pytest backend tests | No frontend/E2E tests | Backend safer | Add frontend/E2E |
| Documentation/AI context file | Partial | README/docs reports | Must keep updated | Important for continuity | Update after changes |

## 22. Mapping to Professor’s Report Methods

### Horizontal Access-Control Matrix

The current system can generate a matrix-like answer if the user asks for it, but it is LLM-generated from retrieved snippets/wiki context, not computed by a static analyzer. Reliability depends on retrieval quality and model behavior. Missing: structured matrix schema and deterministic endpoint/role extraction.

### Vertical Helper Analysis

The system can discuss helper chains if retrieved chunks include helper functions and the LLM infers the relationship. There is no real call graph. Missing: static call graph, caller/callee indexing, and verified helper-chain evidence.

### Requirement-to-Code Trace

The user can ask requirement-style questions and receive evidence-backed answers if retrieval finds matching chunks. It is not a formal requirement trace engine. Missing: structured requirement objects, trace IDs, and coverage status.

### Permission/Policy Mapping

The system detects many permission/policy terms across Android, Spring, FastAPI, Node/Nest, configs, and SELinux files. It does not deeply parse Android manifests, SELinux allow rules, Spring filter chains, FastAPI dependency graphs, or Nest guards. Missing: domain-specific parsers and computed policy maps.

### Evidence-Backed Audit Artifact

The Security Wiki and export together can act as an early audit artifact. It is not yet structured enough for a polished thesis report. Missing: validated wiki schema, stronger evidence references, and nicer export formatting.

### Human-in-the-Loop Evaluation

Manual verification and comparison scoring exist. They are for evaluation/reporting only and do not train models. Missing: richer evaluator workflow, scoring summaries, and aggregate comparison charts.

## 23. What Has Been Achieved

Frontend:

- Login, project list, import forms, workspace, file tree, Monaco viewer.
- Discovery cards and selected candidate flow.
- Chat, evidence cards, wiki display, model health, comparison, feedback, export.
- Evidence cards can open and highlight source ranges.

Backend:

- FastAPI app with auth, project, model health, and export routes.
- SQLite schema and migrations.
- GitHub, ZIP, Android/source-link import.
- Safe ZIP extraction and subfolder path normalization.
- Progress/status fields.

RAG:

- Chroma indexing for code chunks.
- Chroma indexing for wiki chunks.
- Sentence-transformers provider with hash fallback.
- Evidence retrieval combines vector and keyword/security scoring.

LLM:

- Ollama, OpenAI, Gemini, DeepSeek providers.
- Provider health endpoint.
- Structured chat output parsing/validation.

Security Wiki:

- Wiki generation for selected target.
- Wiki storage in SQLite.
- Wiki indexing into Chroma.
- Wiki context used in chat as orientation.

Evaluation:

- Manual verification.
- Model comparison with same evidence package.
- Evaluation persistence and manual scoring fields.

Export:

- Markdown, JSON, and CSV export.

Tests:

- Backend pytest suite exists for core pipeline behavior.

## 24. What Is Missing

Critical:

- Tree-sitter/AST parsing for thesis-grade chunk precision.
  - Why: line ranges and function detection are central to the thesis.
  - Files likely affected: `backend/app/services/parser.py`, tests.
  - Difficulty: high.
  - Recommended fix: add tree-sitter for Java/Go/JS/TS and Python AST or tree-sitter.

- Structured Security Wiki schema.
  - Why: audit artifact should be reliably shaped.
  - Files: `audit_service.py`, `schemas.py`, frontend wiki UI, tests.
  - Difficulty: medium/high.
  - Recommended fix: ask LLM for JSON, validate, render Markdown.

High:

- Stronger retrieval validation and ranked evidence explanation.
  - Why: “no evidence, no answer” depends on evidence quality.
  - Files: `project_service.py`, `vector_index.py`, tests.
  - Difficulty: medium.
  - Recommended fix: improve hybrid retrieval and scoring transparency.

- Large repo queue/cancel/retry.
  - Why: clone/indexing can take time.
  - Files: project service, DB, frontend.
  - Difficulty: high.
  - Recommended fix: add real job table and cancellation.

- Frontend and end-to-end tests.
  - Why: UI evidence opening/highlighting is thesis-critical.
  - Files: frontend test setup.
  - Difficulty: medium.
  - Recommended fix: add Vitest/Playwright.

Medium:

- True module model instead of selected file path.
  - Why: current “module” concept is weak.
  - Files: DB, discovery, frontend.
  - Difficulty: medium.
  - Recommended fix: keep candidate wording now, add modules later.

- Polished export report.
  - Why: supervisor/demo artifact.
  - Files: `audit_service.py`, templates/docs.
  - Difficulty: medium.
  - Recommended fix: structured Markdown report template.

Low:

- Better Chroma telemetry/log handling.
  - Why: reduces confusing logs.
  - Files: config/env docs.
  - Difficulty: low.
  - Recommended fix: document telemetry noise or configure telemetry off if supported.

## 25. What Is Implemented Wrongly or Weakly

- Candidate files are still conceptually called modules in API paths such as `discover-security-modules`.
- There is no real `modules` table or module entity.
- Parser is regex/heuristic despite tree-sitter dependencies being installed.
- Security Wiki is Markdown prompt output, not validated structured data.
- Gemini default model behavior is nuanced: blank env resolves to `gemini-2.5-flash`, but the settings default remains `gemini-1.5-flash`.
- Subfolder import is index-only after shallow clone, not sparse checkout.
- Model comparison is persisted but not yet a polished evaluation dashboard.
- Cost estimation is not meaningful yet.
- Large repo handling has guardrails but no real durable job manager.
- Export is useful but not thesis-report polished.

## 26. Future Roadmap

### Phase 1 — Stabilize Language Support

Tasks:

- Add AST/tree-sitter parsing for Java, Go, JS/TS.
- Add Python AST-based parsing.
- Expand fixtures for Spring, FastAPI, Express/Nest.

Expected result:

- More reliable function/method chunks and line ranges.

Tests:

- Parser fixture tests per language.
- Security detection fixture tests.

Manual verification:

- Import one small repo per language and inspect evidence ranges.

### Phase 2 — Import/Subfolder/Large Repo Handling

Tasks:

- Add true sparse checkout if practical.
- Add job queue/table, cancel, retry, and stage logs.
- Improve large repo warnings.

Expected result:

- Safer handling of large repos and selected Android source folders.

Tests:

- Subfolder import tests.
- timeout/failure tests.

Manual verification:

- Import a selected subfolder from a medium repo.

### Phase 3 — Parser/RAG Quality

Tasks:

- Improve hybrid retrieval.
- Add symbol/call-site metadata.
- Improve ranking explanations.

Expected result:

- More relevant evidence and fewer missed answers.

Tests:

- Retrieval fixture tests with expected top candidates.

Manual verification:

- Ask known RBAC questions and compare returned evidence.

### Phase 4 — Wiki/Schema/Evidence Validation

Tasks:

- Structured `SecurityWiki` JSON schema.
- Validate wiki evidence references.
- Render Markdown from validated JSON.

Expected result:

- Thesis-ready audit artifact.

Tests:

- Wiki JSON validation and fallback tests.

Manual verification:

- Generate wiki and inspect sections/evidence refs.

### Phase 5 — Model Comparison/Evaluation Polish

Tasks:

- Improve side-by-side UI.
- Add scoring summaries.
- Export comparison tables cleanly.

Expected result:

- Stronger evaluation chapter support.

Tests:

- Persisted comparison/scoring tests.

Manual verification:

- Compare Ollama/Gemini/OpenAI on same evidence.

### Phase 6 — Android Case-Study Polish

Tasks:

- Curate selected Android source packages.
- Improve Android permission/policy keyword sets.
- Add Android-specific report templates.

Expected result:

- Realistic Android thesis demo without full AOSP.

Tests:

- Android fixture parsing/detection tests.

Manual verification:

- Analyze selected AccountManagerService-style source.

### Phase 7 — Tests/Documentation/Final Demo

Tasks:

- Add frontend/E2E tests.
- Update README and AI context report after changes.
- Prepare demo script.

Expected result:

- Stable final MVP presentation.

Tests:

- Backend pytest, frontend build/tests, Playwright smoke test.

Manual verification:

- Full import-discovery-wiki-chat-export demo.

## 27. Instructions for Next Developer/AI Assistant

- Read this report before making changes.
- Update this report or the active AI context document after significant implementation changes.
- Do not remove evidence-first behavior.
- Preserve the rule: no source-code evidence, no answer.
- Do not invent or describe self-training from manual feedback.
- Keep the local MVP scope.
- Do not add full multi-user auth.
- Do not attempt full Android AOSP import yet.
- Add or update tests with every feature.
- Implement incrementally and verify each phase.
- Be honest in docs: mark partial features as partial.

## 28. Final Summary

For a developer: the MVP has a real full-stack pipeline now: import, index, detect, discover, generate wiki, retrieve evidence/wiki context, chat, compare models, store feedback, and export. The next technical work should focus on parser quality, structured wiki validation, retrieval quality, frontend/E2E tests, and polished reporting.

For a supervisor: Security CodeWiki currently demonstrates the intended thesis idea: an auditor imports code, finds security-relevant candidate files, generates a Security Wiki, asks questions, and receives answers backed by exact source-code evidence. It is on track, but still needs stronger parsing, validation, and evaluation polish before it should be presented as thesis-ready.

Top 5 next tasks:

1. Replace/augment heuristic parsing with AST/tree-sitter parsing.
2. Add structured validated Security Wiki output.
3. Improve retrieval ranking and evidence quality.
4. Add frontend/end-to-end tests for evidence opening and highlighting.
5. Polish export into a thesis-quality audit report.
