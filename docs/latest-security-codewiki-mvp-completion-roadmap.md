# Security CodeWiki MVP — Current Status, Gaps, and Completion Roadmap

**Date:** 2026-05-05  
**Project:** Security CodeWiki  
**Purpose of this report:** Compare the current MVP against the original thesis plan, clearly state what is achieved, what is wrong or missing, and provide a practical phased Codex implementation command with verification tests.

---

## 1. Executive Summary

The MVP is **on the correct thesis path**, but it is **not complete yet**.

It currently works as a local web application that can import source code, browse files, index chunks, detect security-relevant candidate files, generate a Security Wiki for a selected candidate, answer questions using retrieved source-code evidence, open evidence in the code viewer, compare models, store manual audit feedback, and export reports.

However, several features remain incomplete or fragile:

1. **Python/FastAPI support is missing** because `.py` files are not currently indexed.
2. **JavaScript/TypeScript support is also not present**, so Node/Express/NestJS projects are not handled properly.
3. **The parser is still regex/heuristic**, not tree-sitter-based as planned.
4. **Discovery returns candidate files, not true architectural modules.** This is acceptable for MVP if clearly labelled, but it must not be overclaimed.
5. **Horizontal access-control matrices, vertical helper chains, and requirement-to-code traces are mostly LLM-generated from retrieved snippets**, not computed by static/static-analysis logic.
6. **The Security Wiki is useful but still not fully structured and validated.** It is generated as Markdown and indexed, but it should eventually be generated through a schema with evidence references.
7. **Large repository support is partial.** Full Android AOSP is not realistic yet; selected Android source packages/subfolders are the right target.
8. **Subfolder import is missing**, but it is important because Android and many OSS repos are too large for whole-repo indexing.
9. **Progress percentage for cloning/indexing is missing**, and the user wants clearer loader/progress feedback.
10. **Gemini configuration needs a safe default model** so a free API key can work without the user knowing the model name.
11. **Testing is incomplete**, especially for Python, JavaScript/TypeScript, subfolder import, Gemini defaults, frontend flows, and integration paths.

The next implementation should not overbuild. It should complete the MVP in practical phases: language support, subfolder import and progress, Gemini defaults, parser improvements, Security Wiki schema, model comparison/export polish, and tests.

---

## 2. Original Thesis Plan — What the MVP Was Supposed to Do

The thesis plan defines Security CodeWiki as a local web-based MVP for evidence-backed LLM/RAG-assisted access-control comprehension in OSS projects. The original main goal was to help auditors understand where and how access control is implemented by importing a repo, browsing source files, detecting security-relevant modules/files, generating a Security Wiki, asking evidence-backed questions, comparing LLMs, and exporting an audit report.

The planned user-facing flow was:

1. Superuser login from `.env`.
2. Import a public GitHub repository, ZIP file, or selected Android source package.
3. Browse imported source files.
4. Parse code into method/function-level chunks with backend-generated line ranges.
5. Detect security-relevant files/modules.
6. Let user select a target module/service/file.
7. Generate a Security Wiki for that selected target.
8. Index source-code chunks and wiki chunks into RAG.
9. Ask audit questions in chat.
10. Retrieve source evidence first.
11. Send only evidence + optional wiki context to the LLM.
12. Return answer with file path, function/block, line range, code snippet, explanation, helper chain where supported, confidence/limitations, and open-in-viewer button.
13. Compare answers from multiple LLMs.
14. Store manual audit feedback/verdicts for evaluation/reporting only.
15. Export audit report.

The plan’s core principle remains:

> **No evidence, no answer.**

If the system cannot retrieve source-code evidence, it should not pretend to know the answer.

---

## 3. What We Have Achieved

### 3.1 Working MVP Foundation

Achieved:

- React + TypeScript + Vite frontend.
- FastAPI backend.
- SQLite metadata database.
- Chroma vector store.
- Monaco source viewer.
- Env-based superuser login.
- GitHub import.
- ZIP upload.
- Android source-link/case-study mode.
- File tree and file content viewer.
- Backend-generated code chunks with line ranges.
- Security keyword/tag detection.
- Candidate Security Files / Modules discovery.
- User can select a candidate file/module for focused analysis.
- Security Wiki generation for selected candidate.
- Wiki pages are stored.
- Wiki chunks are indexed into Chroma.
- Chat retrieves raw code evidence first.
- Chat can also use wiki context as orientation.
- Evidence cards are backend-generated from stored chunks.
- Evidence cards include file path, line range, snippet, tags, and critical lines.
- Evidence cards can open actual files in Monaco and highlight relevant ranges.
- LLM providers exist: Ollama/Qwen, OpenAI, Gemini, DeepSeek.
- Provider health/status exists.
- Model comparison exists in a partial but useful form.
- Manual audit feedback exists.
- Export exists as Markdown/JSON/CSV.
- Backend tests exist for some core paths.

### 3.2 Important Improvements Already Completed Since Earlier Reports

Earlier reports showed that wiki chunks were not indexed and chat retrieved only raw code. The current report says this has improved: generated wiki chunks are now indexed into Chroma and chat can retrieve wiki context. That means the project has moved closer to the original Security CodeWiki architecture.

Earlier reports also showed no structured validation. The current report says structured chat validation has been added for evidence IDs, although the wiki itself is still not fully structured/validated.

---

## 4. What Is Implemented Wrong or Too Weakly

This section is direct and honest. These are not failures, but they must be fixed or clearly scoped before thesis evaluation.

### 4.1 `.py` Files Are Not Indexed

This is the immediate practical bug you noticed yesterday.

When you tried FastAPI/Python repos, the system indexed only files like `README.md` and `requirements.txt`. The reason is that `.py` is not currently included in the allowed source extensions.

**Impact:**

- FastAPI repos do not work.
- Django repos do not work.
- Python security/RBAC examples cannot be used.
- The project cannot honestly claim Python support.

**Fix needed:**

- Add `.py` to allowed extensions.
- Detect language as `python`.
- Ignore Python runtime folders such as `.venv`, `venv`, `env`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `site-packages`, `.tox`, `.nox`.
- Add Python parser/chunker for `def`, `async def`, and `class` using indentation-aware block detection or tree-sitter.
- Add FastAPI/Django security keywords.
- Add tests.

### 4.2 JavaScript/TypeScript Are Not Supported Yet

The original allowed file types focused mainly on Java, Go, C/C++, Android-related files, configs, and docs. But you now want broader practical OSS coverage.

**Current likely missing source types:**

- `.js`
- `.jsx`
- `.ts`
- `.tsx`
- `.mjs`
- `.cjs`

**Impact:**

- Node/Express projects do not work properly.
- NestJS projects do not work properly.
- React frontend auth/security code is not indexed.
- Many GitHub OSS examples are missed.

**Fix needed:**

- Add JS/TS file inclusion.
- Ignore JS dependency/build folders: `node_modules`, `.next`, `dist`, `build`, `coverage`, `.turbo`, `.parcel-cache`.
- Add parser/chunker support for functions, classes, arrow functions, exports, Express/Nest route handlers.
- Add security keywords: `jwt`, `passport`, `express-jwt`, `requireAuth`, `authorize`, `isAdmin`, `roles`, `permissions`, `middleware`, `router.get`, `router.post`, `app.use`, `canActivate`, `UseGuards`, etc.
- Add tests.

### 4.3 Parser Is Regex/Heuristic, Not Tree-Sitter

The original plan recommended tree-sitter because it provides reliable source ranges without compiling the project. The current report says tree-sitter dependencies exist, but the parser is still regex/fallback-based.

**Impact:**

- Method detection may fail for complex signatures.
- Python is not supported.
- Java annotations/multiline methods may be missed.
- C++ complex methods may be poorly chunked.
- JavaScript/TypeScript support will be weak unless improved.
- Evidence line ranges may be coarse.

**Practical MVP fix:**

Do not try to build a perfect parser for every language at once. Implement either:

1. **Tree-sitter for Java, Python, JavaScript/TypeScript, Go first**, with fallback to regex/line chunks; or
2. **A practical parser layer per language** with tests, then migrate to tree-sitter later.

Given this is an MVP, the best route is:

- Add Python + JS/TS regex/indentation parser now.
- Add tree-sitter incrementally for Java/Python/JS/TS/Go as Phase 2.

### 4.4 Discovery Returns Files, Not True Modules

Discovery currently groups by file path and calls those “Candidate Security Files / Modules.” This is acceptable for MVP if the UI explains it clearly.

**Impact:**

- It is not a true module detector.
- It may not understand architecture/package boundaries.
- It may miss related support files.

**Keep as MVP wording:**

Use “Candidate Security Files / Modules.” Explain that in MVP, a selected module means a selected candidate file and its related evidence.

**Future fix:**

Add module grouping later based on:

- package/folder,
- class/interface,
- import/call relationships,
- route/controller/service grouping,
- Android service package grouping.

### 4.5 Horizontal/Vertical Analysis Is Not Computed

Professor’s report used horizontal analysis and vertical helper analysis:

- horizontal: exported API/entry point → access-control checks → evidence,
- vertical: helper → subhelper → deeper checks → evidence.

Your MVP prompts the LLM to produce these from retrieved snippets, but it does not compute them using static analysis.

**Impact:**

- Useful for comprehension, but not fully reliable.
- The thesis must say this is LLM-assisted and evidence-grounded, not a guaranteed static analyzer.

**Future fix:**

Add structured extraction of:

- entry points,
- permission checks,
- helper calls,
- route decorators/annotations,
- function call references,
- selected static call expansion for one language/case study.

### 4.6 Security Wiki Is Markdown-Based, Not Fully Structured

The Security Wiki is generated and indexed, but still mostly Markdown-prompt based.

**Impact:**

- Export quality varies.
- Evaluation is harder.
- Matrix/helper-chain data is not consistently machine-readable.

**Fix needed:**

Ask the LLM for JSON conforming to a schema, then render Markdown from that JSON.

Suggested schema:

- title,
- module_path,
- security_goal,
- overview,
- protected_assets,
- public_entry_points,
- access_control_matrix,
- helper_chains,
- requirement_traces,
- evidence_references,
- limitations,
- needs_review.

### 4.7 Large Repo Handling Is Still Partial

The current MVP has some limits: shallow clone, file size limit, max file/chunk counts, repo size guardrails, timeouts. But it still lacks selected subfolder import, true progress percentage, cancellation, queue management, and pagination.

**Impact:**

- Full Android AOSP is not realistic.
- Large repos may be slow or fail.
- User may not know where repo is being cloned or what is happening.

**Fix needed:**

- Add subfolder import.
- Add clone/index progress tracking with stages and percentages where possible.
- Add visible project storage path.
- Add large repo warnings and recommended selected-subfolder mode.

### 4.8 Gemini Model Name Confusion

You added a Gemini free-tier API key in `.env`, but the system also asks for a model name.

This should be fixed in code by providing a safe default model. As of Google’s Gemini API models page, `gemini-2.5-flash` is a stable example model string and is described as a best price-performance model for low-latency, high-volume reasoning tasks. The official docs also list model naming patterns and examples such as `gemini-2.5-flash`.

**Practical fix:**

- If `GEMINI_API_KEY` exists but `GEMINI_DEFAULT_MODEL` is empty, default to:

```env
GEMINI_DEFAULT_MODEL=gemini-2.5-flash
```

- UI should not require the user to know a Gemini model name.
- Model override should stay optional.
- Health check should say: “Gemini API key configured; default model = gemini-2.5-flash.”

---

## 5. Can the MVP Handle All File Types Like Java, C++, Python, and JavaScript?

### Honest Answer

**No, not yet.**

Current support is strongest for:

- Java/Spring-like projects,
- Go projects,
- C/C++ files at a basic heuristic level,
- Android-related selected source files: `.java`, `.aidl`, `.cpp`, `.c`, `.h`, `.te`, `.xml`, `.json`, `.yaml`, Markdown/TXT.

Current missing or weak support:

- Python/FastAPI/Django: missing because `.py` is not indexed.
- JavaScript/TypeScript/Node/NestJS: missing unless extensions were added after the report.
- C++: included but parser is basic.
- Android full AOSP: not feasible yet.

### What Should MVP Support?

For a practical thesis MVP, support these language families:

| Language / Stack | Required for MVP? | Reason |
|---|---:|---|
| Java / Spring | Yes | Good small OSS access-control test cases; similar to Android Java style. |
| Python / FastAPI / Django | Yes | You already tried FastAPI RBAC repos; must fix `.py`. |
| JavaScript / TypeScript / Node / Express / NestJS | Yes | Common OSS auth/RBAC projects. |
| Go / Kubernetes | Yes/Partial | Useful future case study. |
| Android Java / AIDL / XML / SELinux `.te` | Yes/Partial | Main thesis direction. |
| C/C++ | Partial | Needed for ServiceManager/native examples, but parser may stay heuristic. |

Do not promise perfect understanding of every language. The MVP can claim **source indexing and evidence retrieval support** for these file types, with stronger parser quality for selected languages.

---

## 6. What Is Left From the Whole Thesis Plan?

### 6.1 Implemented or Mostly Implemented

| Thesis Plan Item | Status |
|---|---|
| Local MVP | Implemented |
| Superuser login from `.env` | Implemented |
| Public GitHub import | Implemented |
| ZIP upload | Implemented |
| Android source/case-study mode | Partial |
| File tree and Monaco viewer | Implemented |
| Backend-generated line ranges | Implemented |
| RAG indexing with Chroma | Implemented |
| Semantic embeddings | Implemented if package/model available; fallback exists |
| Security candidate discovery | Implemented as file-level candidate discovery |
| User-selected target file/module | Implemented as selected path |
| Security Wiki generation | Implemented, but not fully structured |
| Wiki indexed into RAG | Implemented |
| Evidence-backed chat | Implemented |
| Evidence cards with snippets | Implemented |
| Open evidence in viewer | Implemented |
| Critical line highlighting | Implemented heuristically |
| Multi-model providers | Implemented |
| Model comparison | Partial/implemented |
| Manual feedback | Implemented as audit/evaluation labels |
| Export | Partial/implemented |

### 6.2 Missing or Incomplete

| Thesis Plan Item | Missing / Problem |
|---|---|
| Robust method-level parser | Regex/fallback instead of tree-sitter. |
| Python/FastAPI support | `.py` not indexed. |
| JavaScript/TypeScript support | Not included yet. |
| Full Android handling | Full AOSP not feasible; selected packages needed. |
| Subfolder clone/import | Missing. |
| Clone/index percentage progress | Missing. |
| Strong Security Wiki schema | Missing. |
| Computed horizontal matrix | LLM-generated only. |
| Computed vertical helper chain | LLM-generated only. |
| Requirement-to-code schema | Missing. |
| Better large repo strategy | Partial. |
| Polished thesis report export | Partial. |
| Frontend tests | Missing. |
| Broader backend/integration tests | Partial. |
| VS Code DB extension suggestion | Not part of app; documentation/tooling needed. |

---

## 7. Future Roadmap

### Phase 1 — Immediate Bug Fix and Language Support

Goal: Make small Python/FastAPI and JS/TS security repos usable.

Tasks:

1. Add `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.cjs` to allowed file extensions.
2. Add language detection for Python, JavaScript, TypeScript.
3. Add ignored folders:
   - Python: `.venv`, `venv`, `env`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `site-packages`, `.tox`, `.nox`.
   - JS/TS: `node_modules`, `.next`, `dist`, `build`, `coverage`, `.turbo`, `.parcel-cache`.
4. Add Python parser/chunker for `def`, `async def`, and `class`.
5. Add JS/TS parser/chunker for functions, classes, arrow functions, exported functions, route handlers.
6. Add FastAPI/Django security keywords.
7. Add Express/NestJS security keywords.
8. Add tests.

### Phase 2 — Gemini Free API Key Default Model Fix

Goal: User should only need to add `GEMINI_API_KEY`; model name should have a safe default.

Tasks:

1. If `GEMINI_DEFAULT_MODEL` is empty, default to `gemini-2.5-flash`.
2. Update `.env.example`.
3. Update README.
4. Update `/api/models/health` to show resolved Gemini model.
5. Keep model override optional.
6. Add tests for Gemini default behavior.

### Phase 3 — Subfolder Import and Large Repo UX

Goal: Avoid cloning/indexing entire massive repos.

Tasks:

1. Add optional `subfolder_path` field during project import.
2. For GitHub repos, implement sparse checkout or clone + checkout selected subfolder.
3. Store selected subfolder in project metadata.
4. Show where repo is cloned locally.
5. Show cloning/indexing stages.
6. Add approximate progress percentage:
   - fetching/clone started,
   - clone completed,
   - scanning files,
   - indexing file X / total,
   - embedding chunk X / total,
   - indexed.
7. Add large repo warning before import.
8. Add tests.

### Phase 4 — Parser Quality Improvement

Goal: Improve line-range quality and symbol extraction.

Tasks:

1. Introduce tree-sitter parser service for Java, Python, JS/TS, Go.
2. Keep current regex/fallback as backup.
3. Add tests with annotated/multiline functions.
4. Add language-specific route/entry-point extraction where practical.

### Phase 5 — Structured Security Wiki

Goal: Make Security Wiki a stable thesis artifact.

Tasks:

1. Add `SecurityWiki` Pydantic schema.
2. Ask LLM for JSON.
3. Validate evidence references by chunk ID.
4. Render Markdown from validated JSON.
5. Store raw model response, parsed wiki JSON, validation status.
6. Export structured matrix/helper-chain sections.
7. Add tests.

### Phase 6 — Thesis Evaluation Polish

Goal: Make final demo and supervisor review strong.

Tasks:

1. Improve model comparison layout.
2. Improve export report formatting.
3. Add aggregated evaluation summary.
4. Add frontend tests.
5. Add integration test with small fixture repo.
6. Add README and user guide.

---

## 8. Developer Notes: Database and Storage

### Where Is SQLite DB?

Default:

```text
./storage/security_codewiki.db
```

If backend runs from the `backend` folder, it becomes:

```text
backend/storage/security_codewiki.db
```

### How to Open DB

Use one of:

1. **VS Code extension:** `SQLite Viewer` or `SQLite` extension.
2. **DB Browser for SQLite** desktop app.
3. Python script with `sqlite3`.
4. SQLite CLI.

Ask Codex to update README and `.vscode/extensions.json` with recommended extensions:

- SQLite Viewer / SQLite extension.
- Python extension.
- Pylance.
- ESLint/Prettier if frontend uses them.

### What Is Stored?

- Projects.
- Files.
- Code chunks.
- Wiki pages.
- Chat sessions/messages.
- Evidence JSON.
- Model comparison/evaluation rows.
- Manual feedback/verifications.

### How Long Is Data Stored?

Until you delete:

- SQLite rows,
- project folder,
- Chroma collection,
- or the whole storage folder.

There is no automatic expiry.

### Why Are `projects`, `chroma`, or `exports` Folders Empty?

- `projects` is empty until a repo/ZIP is imported.
- `chroma` is empty until indexing creates vector collections.
- `exports` may stay empty because exports are streamed from API and not saved permanently.
- You may also be looking at the wrong storage root. If backend runs from `backend/`, the actual folders may be under `backend/storage/`.

### Recommended Fix

Use absolute storage paths in `.env` so there is no confusion:

```env
PROJECT_STORAGE_PATH=C:/path/to/SecurityCodeWiki/backend/storage/projects
CHROMA_DB_PATH=C:/path/to/SecurityCodeWiki/backend/storage/chroma
SQLITE_DB_PATH=C:/path/to/SecurityCodeWiki/backend/storage/security_codewiki.db
EXPORT_STORAGE_PATH=C:/path/to/SecurityCodeWiki/backend/storage/exports
```

---

## 9. Practical Verification Checklist for You

After Codex implements the next phase, verify like this.

### 9.1 Python Repo Test

Use a small Python/FastAPI RBAC repo.

Expected:

- File tree shows `.py` files.
- SQLite `files` table contains `.py` rows.
- SQLite `code_chunks` table contains Python function/class chunks.
- Discovery with `role based access control endpoints` returns Python files such as `main.py`, `auth.py`, `security.py`, `dependencies.py`, `rbac_config.py`, etc., depending on repo structure.
- Chat returns Python evidence cards with file path, function/block, line range, snippet.

### 9.2 JavaScript/TypeScript Repo Test

Use a small Express/NestJS auth/RBAC repo.

Expected:

- File tree shows `.js`, `.ts`, etc.
- Discovery returns route/middleware/auth files.
- Chat returns route/middleware evidence cards.

### 9.3 Gemini Test

In `.env`, add only:

```env
GEMINI_API_KEY=your_key_here
```

Leave `GEMINI_DEFAULT_MODEL` empty or remove it.

Expected:

- Health check shows Gemini configured.
- Resolved model should be `gemini-2.5-flash`.
- Chat with Gemini should work without manual model override.

### 9.4 Subfolder Import Test

Use a repo with a known subfolder.

Expected:

- User can enter repo URL + subfolder path.
- Only subfolder content is indexed.
- Project details show selected subfolder.
- Storage path is visible.

### 9.5 Progress Loader Test

During import/index:

Expected:

- UI shows stage.
- UI shows approximate percentage or file/chunk count progress.
- If clone/index fails, status message explains why.

---

## 10. Codex Implementation Command

Copy this full command into Codex.

```text
You are working on my thesis MVP project: Security CodeWiki.

Do not rebuild the app from scratch.
Do not change the thesis direction.
Do not add full multi-user auth.
Do not add self-training.
Do not implement exploit generation.
Do not attempt full Android AOSP support.

Goal:
Complete the next MVP phase by fixing language support, Gemini default model behavior, subfolder import/progress UX, storage clarity, and tests.

Context:
Security CodeWiki is a local web MVP for evidence-backed LLM/RAG-assisted access-control comprehension. It imports source code, indexes line-aware chunks, detects candidate security files/modules, generates a Security Wiki, answers chat questions using source evidence, compares models, and exports reports.

Current known issues:
1. Python repos are not indexed because .py files are not included.
2. JavaScript/TypeScript repos are not supported.
3. Gemini free API key requires a model name, but the user may not know the model.
4. Large repos need subfolder import support.
5. Cloning/indexing progress needs a clearer loader/progress status.
6. Repo storage path should be visible.
7. SQLite DB visibility should be documented, and VS Code recommended extensions should be added.
8. Tests need to cover the new functionality.

====================================================
PHASE 1 — Add Python and JavaScript/TypeScript file support
====================================================

1. Update file inclusion rules.

Add allowed extensions:
- .py
- .js
- .jsx
- .ts
- .tsx
- .mjs
- .cjs

Ensure language detection returns:
- python
- javascript
- typescript
- jsx/tsx if useful, or javascript/typescript with variant.

2. Update ignored folders/patterns.

Add Python ignores:
- .venv
- venv
- env
- __pycache__
- .pytest_cache
- .mypy_cache
- .ruff_cache
- site-packages
- .tox
- .nox

Add JS/TS ignores if not already present:
- node_modules
- .next
- dist
- build
- coverage
- .turbo
- .parcel-cache

3. Add parser/chunker support.

Python:
- Extract class definitions: class MyClass:
- Extract normal functions: def function_name(...):
- Extract async functions: async def function_name(...):
- Use indentation-based block detection to calculate end_line.
- chunk_type values: class, function, async_function.
- Preserve backend-generated line ranges.
- Fallback to existing line_range_fallback if parsing fails.

JavaScript/TypeScript:
- Extract function declarations.
- Extract exported functions.
- Extract classes.
- Extract common arrow function assignments.
- Extract Express-style route handlers where practical:
  - app.get(...)
  - app.post(...)
  - router.get(...)
  - router.post(...)
  - router.use(...)
- Extract NestJS decorators where practical:
  - @Controller
  - @Get
  - @Post
  - @UseGuards
- Preserve backend-generated line ranges.
- Fallback to existing line_range_fallback if parsing fails.

4. Update security detection.

Add Python/FastAPI/Django keywords:
- Depends
- Security
- HTTPBearer
- OAuth2PasswordBearer
- APIKeyHeader
- APIKeyCookie
- JWT
- jwt
- decode_token
- create_access_token
- get_current_user
- get_current_active_user
- require_role
- require_permission
- role
- roles
- permission
- permissions
- is_admin
- admin_required
- authenticated
- authorize
- authorization
- authentication
- HTTPException
- status.HTTP_401_UNAUTHORIZED
- status.HTTP_403_FORBIDDEN
- 401
- 403
- APIRouter
- @app.get
- @app.post
- @router.get
- @router.post
- dependencies=
- scopes
- permission_classes
- IsAuthenticated
- IsAdminUser

Add JavaScript/TypeScript/Node/Nest keywords:
- jwt
- verify
- sign
- passport
- express-jwt
- requireAuth
- requireRole
- requirePermission
- authorize
- authorization
- authentication
- middleware
- isAdmin
- roles
- permissions
- hasRole
- hasPermission
- router.get
- router.post
- app.get
- app.post
- app.use
- protect
- guard
- CanActivate
- UseGuards
- AuthGuard
- JwtAuthGuard
- RolesGuard
- ForbiddenException
- UnauthorizedException
- 401
- 403

Map to existing tags:
- potential_entry_point
- potential_access_check
- potential_helper
- potential_config_file

====================================================
PHASE 2 — Fix Gemini default model behavior
====================================================

Problem:
User has a Gemini free-tier API key but does not know the model name.

Requirements:
1. If GEMINI_API_KEY is set and GEMINI_DEFAULT_MODEL is empty/missing, use default:
   gemini-2.5-flash
2. Update backend settings/config accordingly.
3. Update model health endpoint to show resolved Gemini model.
4. Update frontend to show Gemini as ready when API key exists and a resolved default model is available.
5. Keep optional model override in UI.
6. Update .env.example and README.
7. Add tests.

====================================================
PHASE 3 — Add selected subfolder import for GitHub repos
====================================================

Goal:
Support large repos by allowing the user to import/index only a selected subfolder.

Requirements:
1. Add optional subfolder_path field to project creation.
2. Add frontend input:
   Optional subfolder path, e.g.:
   src/main/java
   services/core/java/com/android/server/accounts
3. Backend should store subfolder_path in projects table or project metadata.
4. Implement practical approach:
   Preferred: git sparse checkout for selected subfolder.
   Acceptable MVP fallback: shallow clone then index only selected subfolder.
5. Ensure safe path handling to prevent path traversal.
6. If subfolder does not exist, mark project failed with clear message.
7. In project details/status UI, show:
   - repo URL
   - selected subfolder if any
   - local clone path
8. Add tests.

Important:
Do not attempt full Android AOSP import. Subfolder import is the practical path.

====================================================
PHASE 4 — Progress loader and status clarity
====================================================

Current problem:
Cloning/indexing can take time, and user needs clearer progress.

Requirements:
1. Extend project status/status_message to include stages:
   - created
   - fetching
   - fetched
   - scanning
   - indexing_files
   - indexing_chunks
   - embedding
   - indexed
   - failed
2. Add optional progress fields if feasible:
   - progress_percent
   - files_indexed
   - total_files
   - chunks_indexed
   - total_chunks
   - current_file
3. If schema migration is needed, add it safely.
4. Frontend should show loader/progress bar where possible.
5. If exact percentage is not possible during git clone, show indeterminate loader and message:
   "Cloning repository... progress percentage is unavailable until clone completes."
6. Once scanning/indexing begins, show file/chunk counts and approximate percentage.
7. Add tests for status updates if practical.

====================================================
PHASE 5 — Storage and DB developer clarity
====================================================

Requirements:
1. In README, clearly document:
   - SQLite DB path.
   - Chroma path.
   - project repo storage path.
   - export behavior.
   - why storage folders may be empty.
   - why backend/storage may differ from root storage.
2. Add .vscode/extensions.json with recommended extensions:
   - Python
   - Pylance
   - SQLite Viewer or SQLite extension
   - ESLint/Prettier if frontend uses them
3. Do not require these extensions at runtime.
4. Add a small docs section: "How to inspect the database".

====================================================
PHASE 6 — Tests
====================================================

Add/update backend tests for:

1. File filtering:
   - .py included
   - .js/.ts included
   - venv/__pycache__/site-packages ignored
   - node_modules/dist/.next ignored

2. Parser:
   - Python def extraction
   - Python async def extraction
   - Python class extraction
   - JS function extraction
   - JS/TS arrow function extraction if implemented
   - Express router/app handler extraction if implemented
   - Correct start_line/end_line for each

3. Security detection:
   - FastAPI Depends/OAuth2/JWT/status 403 patterns tagged
   - Express/Nest auth/guard/role patterns tagged

4. Indexing:
   - small Python fixture creates code chunks beyond README/requirements
   - small JS fixture creates code chunks
   - Chroma receives vectors for new language chunks

5. Discovery:
   - query "role based access control endpoints" returns Python security file fixture
   - query "jwt validation" returns auth/JWT fixture

6. Gemini config:
   - GEMINI_API_KEY set and GEMINI_DEFAULT_MODEL missing resolves to gemini-2.5-flash
   - health endpoint reports resolved Gemini model

7. Subfolder import:
   - selected subfolder indexes only files under subfolder
   - missing subfolder fails clearly

8. No-evidence chat:
   - still refuses answer when no code evidence exists

Run command:
cd backend
python -m pytest

If frontend test framework already exists, add basic frontend tests for:
- subfolder input renders
- progress bar/status renders
- model health shows Gemini resolved model
- discovery cards display Python/JS candidates

====================================================
PHASE 7 — Documentation update
====================================================

Update README with:
- current MVP purpose
- supported languages/file types
- unsupported/full Android limitation
- recommended small repos for testing
- Gemini default model behavior
- local Qwen/Ollama setup
- storage paths
- DB inspection with VS Code extension
- subfolder import usage
- progress status explanation
- how to run tests



Most important immediate fix:

> Add Python and JavaScript/TypeScript support, because your tool currently cannot handle common OSS security repos outside Java/Go/Android-style source.

Most important architecture fix:

> Add subfolder import and better progress tracking, because large repos like Android cannot be handled as full repository imports.

Most important research-quality fix:

> Move Security Wiki and chat outputs toward structured schemas with evidence references, because the thesis depends on evidence-grounded auditing, not generic LLM prose.

Most important usability fix:

> Add Gemini default model behavior so a user with only a free API key can use Gemini without knowing model names.

If you complete these phases, the MVP will be much closer to the original thesis plan and suitable for a supervisor demo.
