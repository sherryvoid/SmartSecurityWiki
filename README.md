# Security CodeWiki

Local MVP for evidence-backed security audit documentation and chat.

## Project Goal

Security CodeWiki is a local thesis MVP for security auditors. It imports source code, indexes code chunks with backend-generated line numbers, detects candidate security files/modules, generates a Security Wiki for a selected candidate, and answers questions only after retrieving source-code evidence.

Core rule:

```text
No evidence, no answer.
```

## Run Backend

```powershell
cd backend
copy .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-exclude 

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload


storage/*
```

Default login comes from `.env`:

```env
APP_SUPERUSER_USERNAME=admin
APP_SUPERUSER_PASSWORD=change_me
OLLAMA_DEFAULT_MODEL=qwen3.5:9b
```

Add OpenAI, Gemini, and DeepSeek keys in `.env` later. API keys stay on the backend.

Embedding settings:

```env
EMBEDDING_PROVIDER=sentence-transformers
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
```

Install semantic embedding support:

```powershell
cd backend
python -m pip install -r requirements.txt
```

The preferred embedding provider is local `sentence-transformers`. If the package or model is unavailable, the app falls back to hash embeddings so development can continue. Source code is not sent to a cloud embedding service by default.

Gemini setup:

```env
GEMINI_API_KEY=your_key_here
GEMINI_DEFAULT_MODEL=
```

If `GEMINI_DEFAULT_MODEL` is blank, the backend resolves Gemini to `gemini-2.5-flash`. The frontend still lets you override the model manually when needed.

Run backend tests:

```powershell
cd backend
python -m pytest
```

Model/provider readiness is available at:

```http
GET /api/models/health
```

The response checks Ollama reachability and available models, whether cloud API keys are configured, and which embedding provider is active. API keys are never returned.

Large repository guardrails:

```env
MAX_TOTAL_FILES_TO_INDEX=2000
MAX_TOTAL_CHUNKS_TO_INDEX=10000
MAX_REPO_SIZE_MB=500
GIT_CLONE_TIMEOUT_SECONDS=300
```

The MVP uses shallow Git clones. If limits are reached, the project status message explains whether indexing is partial or failed. Full Android AOSP is not supported; use selected subfolders or curated case-study packages.

Storage paths:

```env
PROJECT_STORAGE_PATH=./storage/projects
CHROMA_DB_PATH=./storage/chroma
SQLITE_DB_PATH=./storage/security_codewiki.db
EXPORT_STORAGE_PATH=./storage/exports
```

Cloned/extracted projects are stored under `PROJECT_STORAGE_PATH`, Chroma vectors under `CHROMA_DB_PATH`, and SQLite metadata at `SQLITE_DB_PATH`. Exports currently stream from the API; `EXPORT_STORAGE_PATH` is reserved for saved export files. Relative paths are resolved from the backend working directory, so absolute paths are recommended for long-running thesis experiments.

## How to Inspect the Database

The SQLite DB stores projects, indexed files, code chunks, wiki pages, chat messages, model comparison evaluations, and manual audit feedback.

Recommended VS Code extensions are listed in `.vscode/extensions.json`, including Python, Pylance, and SQLite Viewer. After indexing a project, open:

```text
backend/storage/security_codewiki.db
```

if you run the backend from the `backend` folder with default relative paths.

If folders look empty, check which working directory started Uvicorn. Relative paths like `./storage/security_codewiki.db` resolve from that working directory, so running from the repo root can create `storage/`, while running from `backend/` creates `backend/storage/`.

## Run Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## MVP Flow

1. Log in with the `.env` superuser.
2. Create a project from GitHub, ZIP, or Android case-study link.
   - For large GitHub/Android repos, use the optional subfolder path, for example `src/main/java` or `services/core/java/com/android/server/accounts`.
3. Wait for indexing.
4. Browse files in Monaco.
5. Discover candidate security files/modules from an audit goal.
6. Select a candidate file/module for analysis.
7. Generate a Security Wiki for the selected candidate.
8. Ask evidence-backed questions.
9. Compare models.
10. Mark answers as Verified, Incomplete, Incorrect, or Needs Review.
11. Export Markdown, JSON, or CSV.

Discovery groups security-relevant files by tags, symbols, and audit-goal terms. For the MVP, a selected module means the selected candidate file and its related evidence.

Security Wiki generation is manual. After generation, wiki sections are stored in SQLite and indexed into Chroma as `source_type="wiki"` context.

Chat retrieves:

- raw source-code evidence chunks
- generated wiki context chunks, when available

Only raw source-code chunks are treated as proof and rendered as evidence cards. Wiki context is orientation only. If no source evidence is found, the system returns `Not verified from the available source-code evidence.`

Evidence cards include file path, symbol, line range, snippet, and critical lines. Clicking an evidence card opens the file in Monaco, scrolls to the evidence range, highlights the full range, and marks critical lines more strongly.

Manual feedback buttons are for audit review and reporting only. They do not self-train or automatically improve any model.

Chat and comparison prompts ask models for structured JSON with retrieved evidence IDs. The backend validates those IDs against retrieved source-code chunks and still renders evidence cards from backend chunks only. If JSON parsing fails, the UI shows a validation warning and keeps backend-generated evidence cards.

Model comparison stores each model answer, evidence package, wiki context, latency, validation status, and optional manual evaluation scores. These scores are for thesis evaluation/reporting only, not model training.

## Supported File Types

The MVP currently indexes:

- Java/Spring and Android Java: `.java`
- Python/FastAPI/Django-style source: `.py`
- JavaScript/TypeScript/Node/Nest-style source: `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs`
- Go: `.go`
- C/C++ and Android native source: `.c`, `.cpp`, `.h`
- Android/interface/policy/config files: `.aidl`, `.xml`, `.te`, `.json`, `.yaml`, `.yml`
- Documentation/context files: `.md`, `.txt`

Ignored folders include dependency/build/runtime folders such as `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `site-packages`, `.pytest_cache`, `.next`, `dist`, `build`, `coverage`, `.turbo`, and `.parcel-cache`.

Full Android AOSP is not supported in this MVP. Use selected subfolders or curated case-study packages.

## Progress Status

Project import/indexing uses these stages:

- `created`
- `fetching`
- `fetched`
- `scanning`
- `indexing_files`
- `indexing_chunks`
- `embedding`
- `indexed`
- `failed`

During Git clone, exact progress percentage is unavailable, so the UI shows an indeterminate loader. During scanning/indexing/embedding, the UI shows approximate percent plus file/chunk counts.

## Rebuild Index

After changing embedding providers or upgrading from old hash vectors, rebuild a project index:

```http
POST /api/projects/{project_id}/rebuild-index
```

This clears and rebuilds the project's file/chunk metadata and Chroma vectors.

## Current Limitations

- Method parsing is still heuristic/regex-based, not full tree-sitter.
- Semantic embeddings require `sentence-transformers` and a local/downloaded model; hash embeddings are fallback only.
- Structured LLM output falls back to raw text if the selected model does not return valid JSON.
- Large repository support is guarded by limits; full Android AOSP is still out of scope for this MVP.
