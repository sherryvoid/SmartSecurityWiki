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
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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

## Rebuild Index

After changing embedding providers or upgrading from old hash vectors, rebuild a project index:

```http
POST /api/projects/{project_id}/rebuild-index
```

This clears and rebuilds the project's file/chunk metadata and Chroma vectors.

## Current Limitations

- Method parsing is still heuristic/regex-based, not full tree-sitter.
- Semantic embeddings require `sentence-transformers` and a local/downloaded model; hash embeddings are fallback only.
- LLM output is still free text and not yet Pydantic-validated JSON.
- Model health checks and full comparison scoring are still pending.
