SecurityCodeWiki - Current Implementation Status Report
Date: 2026-05-22
Audited by: Codex

## Phase 1 - Parser
Status: COMPLETE

tree-sitter Java: YES

tree-sitter Go: YES

Python ast: YES

Regex fallback: YES

Chunk keys complete: YES

Tests passing: 23 passed, 0 failed, 14 errors overall. Parser-specific tests passed.

Gaps remaining:
- The parser implementation is complete for Java, Go, and Python, but the full test suite currently errors during test fixture cleanup because `backend/.pytest_runtime` cannot be removed on Windows.
- The parser has compatibility fallback chunk types such as `route_handler`, `markdown_section`, and `line_range_fallback`; the required chunk keys are present, but not every fallback chunk type is limited to only `method`, `class`, `function`, or `block`.

Detailed audit:
- `tree-sitter` is imported inside language-specific parser functions, not at module top level.
- Java parser imports:
  - `from tree_sitter import Language, Parser`
  - `import tree_sitter_java as tsjava`
- Go parser imports:
  - `from tree_sitter import Language, Parser`
  - `import tree_sitter_go as tsgo`
- Python parser imports:
  - `import ast as python_ast`
- Java tree-sitter parsing is implemented in `_chunk_java_tree_sitter`.
- Go tree-sitter parsing is implemented in `_chunk_go_tree_sitter`.
- Python AST parsing is implemented in `_chunk_python_ast`.
- Regex/heuristic fallback is present through `_chunk_symbols`, `_chunk_python`, `_chunk_javascript`, and `_fallback_chunks`.
- Tree-sitter and AST parser exceptions are caught and logged, then the pipeline falls back to the legacy parser path.
- Chunks expose the required keys: `chunk_id`, `file_path`, `language`, `class_name`, `symbol`, `start_line`, `end_line`, `content`, `chunk_type`, `tags`.
- Line numbers are 1-indexed. Tree-sitter uses `node.start_point[0] + 1`; Python AST uses `node.lineno`; fallback enumeration starts at line 1.

Main parse function signature:

```python
def chunk_source(file_path: str, language: str, code: str) -> list[Chunk]:
```

Exact parser summary:

tree-sitter is USED. Python ast is USED. Regex fallback is PRESENT.

## Phase 2 - Structured Security Wiki
Status: COMPLETE

Pydantic schema exists: YES

LLM instructed to return JSON: YES

JSON repair fallback: YES

chunk_id validation: YES

render_wiki_to_markdown function: YES

Tests passing: 23 passed, 0 failed, 14 errors overall. Structured wiki tests passed.

Gaps remaining:
- The current SQLite database had no rows in `wiki_pages` at audit time, so I could not verify an existing stored wiki row starts with `{`.
- The code stores structured wiki JSON in `wiki_pages.content_markdown`; the column name is legacy and does not reflect the new JSON content.

Detailed audit:
- `backend/app/db/schemas.py` contains `SecurityWikiSchema`.
- Nested wiki models are present:
  - `WikiEntryPoint`
  - `WikiACRow`
  - `WikiHelper`
  - `WikiRequirementTrace`
- `WikiEntryPoint` fields:
  - `name: str`
  - `file_path: str`
  - `start_line: int`
  - `end_line: int`
  - `description: str`
  - `chunk_id: Optional[str] = None`
- `WikiACRow` fields:
  - `caller: str`
  - `permission_check: str`
  - `file_path: str`
  - `start_line: Optional[int] = None`
  - `chunk_id: Optional[str] = None`
- `WikiHelper` fields:
  - `name: str`
  - `file_path: str`
  - `role: str`
  - `chunk_id: Optional[str] = None`
- `WikiRequirementTrace` fields:
  - `requirement: str`
  - `code_reference: str`
  - `file_path: Optional[str] = None`
  - `chunk_id: Optional[str] = None`
- `SecurityWikiSchema` fields:
  - `module_overview: str`
  - `entry_points: list[WikiEntryPoint]`
  - `access_control_matrix: list[WikiACRow]`
  - `vertical_helpers: list[WikiHelper]`
  - `requirement_traces: list[WikiRequirementTrace]`
  - `limitations: str`
  - `generated_at: Optional[str] = None`
- `llm.py` instructs the model to return JSON only.
- `llm.py` includes `SecurityWikiSchema.model_json_schema()` in the prompt.
- `llm.py` validates model output with `SecurityWikiSchema.model_validate_json(...)`.
- `llm.py` has a repair retry prompt: `The previous response was not valid JSON. Return ONLY the JSON object, nothing else.`
- If repair fails, the code falls back to a valid schema object containing the raw response and `parse_failed: True`.
- `audit_service.py` contains `render_wiki_to_markdown(wiki: SecurityWikiSchema) -> str`.
- `audit_service.py` contains chunk ID validation through `validate_wiki_chunk_ids`.

Exact wiki summary:

Wiki schema is STRUCTURED JSON WITH PYDANTIC. LLM JSON repair fallback is PRESENT.

## Phase 3 - Retrieval Quality
Status: COMPLETE

Embedding mode check function: YES

Startup log for embedding mode: YES

test_retrieval_quality.py exists: YES

Fixture files exist: YES

Tests passing: 23 passed, 0 failed, 14 errors overall. Retrieval quality tests passed.

Gaps remaining:
- The integration test currently reports semantic embeddings active on this machine, but thesis evaluation depends on the target machine having the sentence-transformers model cached locally.
- The full test suite still has unrelated fixture cleanup errors.

Detailed audit:
- `backend/app/services/vector_index.py` has `get_embedding_mode() -> str`.
- `backend/app/main.py` logs embedding mode during startup:
  - `[VectorIndex] Embedding mode: %s`
- The active semantic embedding model is configured as `BAAI/bge-small-en-v1.5`.
- Hash fallback exists through `HashEmbeddingProvider`.
- Hash fallback activates when the semantic provider cannot load or when config selects a non-semantic provider.
- `embedding_status()` exposes provider, model, semantic/fallback state, warning, and label.

Backend test files:
- `backend/tests/conftest.py`
- `backend/tests/test_current_pipeline.py`
- `backend/tests/test_export_evaluation.py`
- `backend/tests/test_parser_ast.py`
- `backend/tests/test_retrieval_quality.py`
- `backend/tests/test_structured_wiki.py`

Retrieval quality test functions:
- `test_semantic_chunks_exist_for_auth_service`
- `test_security_tags_are_present_for_permission_and_exception_chunks`
- `test_retrieval_returns_auth_service_for_permission_query`
- `test_embedding_mode_is_semantic`

Fixture files:
- `backend/tests/fixtures/java_auth_sample/AuthService.java`

Exact retrieval summary:

Embedding mode check is PRESENT. Retrieval quality tests are PRESENT. Fixture files are PRESENT.

## Phase 4 - Android Demo Package
Status: COMPLETE

Files present:

Files missing:
- None

Evaluation questions in README: YES, count 5

Detailed audit:
- `AccountManagerService_excerpt.java` contains `Binder.getCallingUid()`.
- `IAccountManager.aidl` contains at least four method signatures: `getAuthToken`, `addAccount`, `removeAccount`, and `getAccounts`.
- `account_policy.te` contains SELinux `allow` rules.
- `AndroidManifest_permissions.xml` declares `android.permission.USE_CREDENTIALS`.
- `README.md` contains five prepared evaluation questions.

Exact Android demo summary:

Android demo package is COMPLETE. Missing files: [].

## Phase 5 - Playwright E2E Tests
Status: INSTALLED

playwright.config.ts: YES

smoke.spec.ts: YES

npm scripts added: YES

Note: Tests not run for this audit because the user specifically requested not to run Playwright tests here. They require both backend and frontend running for a meaningful result.

Detailed audit:
- `@playwright/test` is listed in `frontend/package.json` devDependencies as `^1.44.0`.
- `frontend/playwright.config.ts` exists.
- `frontend/e2e/` exists.
- `frontend/e2e/smoke.spec.ts` exists.
- `frontend/e2e/README.md` exists.
- `test:e2e` script exists.
- `test:e2e:ui` script exists.
- `cd frontend && npx playwright --version 2>&1` returned:

```text
Version 1.60.0
```

Exact Playwright summary:

Playwright is INSTALLED. Smoke test file is PRESENT.

## Phase 6 - Export and Evaluation Polish
Status: COMPLETE

Export endpoint: YES, `/api/projects/{project_id}/export?format=markdown|json|csv`

Structured Markdown sections: YES

CSV export: YES

Scoring rubric fields: YES

Tests passing: 23 passed, 0 failed, 14 errors overall. Export/evaluation tests passed.

Gaps remaining:
- The new structured export and scoring tests pass, but the full suite is blocked by unrelated `.pytest_runtime` cleanup permission errors.
- The export uses best-effort aggregation from current tables; completeness depends on chat, evaluation, wiki, and comparison rows being present for the project.

Detailed audit:
- `backend/app/api/routes/projects.py` exposes:
  - `GET /api/projects/{project_id}/export?format=markdown|json|csv`
  - legacy `GET /api/projects/{project_id}/export/{export_format}`
- Export responses set `Content-Disposition`.
- Markdown export contains these section headers:
  - `# Security Audit Report`
  - `## 1. Project Information`
  - `## 2. Security Wiki`
  - `## 3. Audit Questions and Answers`
  - `## 4. Model Comparison Summary`
  - `## 5. Evaluation Notes`
  - `## 6. System Limitations`
- CSV export for model comparison is present.
- CSV headers are:
  - `question_id`
  - `question_text`
  - `model_provider`
  - `model_name`
  - `evidence_count`
  - `answer_length_chars`
  - `human_correctness_score`
  - `human_evidence_quality_score`
  - `hallucination_flag`
  - `notes`
- `EvaluationScoreRequest` accepts:
  - `correctness`
  - `evidence_quality`
  - `hallucination`
  - `notes`
- SQLite evaluations table includes:
  - `correctness_score`
  - `evidence_quality_score`
  - `hallucination_flag`
- Export-related tests are present in `backend/tests/test_export_evaluation.py`.

Exact export summary:

Export endpoint is PRESENT. Structured Markdown report is IMPLEMENTED. CSV export is PRESENT. Scoring rubric fields are PRESENT.

## Overall Test Results
Command run: `python -m pytest tests/ -v --tb=short`

Result: 23 passed, 0 failed, 14 errors

Failed tests:
- None failed by assertion.

Errored tests:
- `test_db_schema_initialization` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_zip_safe_extraction_allows_normal_zip` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_zip_safe_extraction_rejects_path_traversal` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_file_filtering_includes_python_and_javascript_and_ignores_dependency_dirs` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_chat_no_evidence_returns_safe_not_verified` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_retrieved_evidence_card_integrity` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_wiki_generation_stores_wiki_page` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_verification_manual_feedback_is_stored` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_model_comparison_uses_shared_evidence_package` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_indexing_python_and_javascript_files_creates_chunks` - setup error: Windows `PermissionError` removing `backend/.pytest_runtime`
- `test_discovery_returns_python_and_jwt_security_fixtures` - setup error inferred from progress output
- `test_gemini_empty_default_model_resolves_to_flash` - setup error inferred from progress output
- `test_subfolder_import_indexes_only_selected_subfolder` - setup error inferred from progress output
- `test_missing_subfolder_marks_project_failed` - setup error inferred from progress output

Warnings:
- No warnings appeared in the first 120 lines of the requested pytest output.

Requested test output, first 120 lines:

```text
============================= test session starts =============================
platform win32 -- Python 3.13.13, pytest-8.3.3, pluggy-1.6.0
rootdir: C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend
configfile: pytest.ini
plugins: anyio-4.13.0
collected 37 items

tests\test_current_pipeline.py EEE..E...EEE.EEEEEEE                      [ 54%]
tests\test_export_evaluation.py ....                                     [ 64%]
tests\test_parser_ast.py ....                                            [ 75%]
tests\test_retrieval_quality.py ....                                     [ 86%]
tests\test_structured_wiki.py .....                                      [100%]

=================================== ERRORS ====================================
_______________ ERROR at setup of test_db_schema_initialization _______________
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'

During handling of the above exception, another exception occurred:
tests\conftest.py:15: in isolated_runtime
    shutil.rmtree(runtime)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:790: in rmtree
    return _rmtree_unsafe(path, onexc)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:635: in _rmtree_unsafe
    onexc(os.rmdir, path, err)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'
________ ERROR at setup of test_zip_safe_extraction_allows_normal_zip _________
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'

During handling of the above exception, another exception occurred:
tests\conftest.py:15: in isolated_runtime
    shutil.rmtree(runtime)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:790: in rmtree
    return _rmtree_unsafe(path, onexc)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:635: in _rmtree_unsafe
    onexc(os.rmdir, path, err)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'
______ ERROR at setup of test_zip_safe_extraction_rejects_path_traversal _______
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'

During handling of the above exception, another exception occurred:
tests\conftest.py:15: in isolated_runtime
    shutil.rmtree(runtime)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:790: in rmtree
    return _rmtree_unsafe(path, onexc)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:635: in _rmtree_unsafe
    onexc(os.rmdir, path, err)
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'
_ ERROR at setup of test_file_filtering_includes_python_and_javascript_and_ignores_dependency_dirs _
C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.13_3.13.3568.0_x64__qbz5n2kfra8p0\Lib\shutil.py:631: in _rmtree_unsafe
    os.rmdir(path)
E   PermissionError: [WinError 5] Access is denied: '\\?\C:\Users\shahe\Downloads\Thesis\SecurityWiki\backend\.pytest_runtime'
```

Lines mentioning requested keywords:
- `tests\test_retrieval_quality.py ....                                     [ 86%]`

Tests: 23 passed, 0 failed, 14 errors. Failed tests: []. Warnings: [].

## SQLite Schema Summary
`projects`
- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `source_type TEXT NOT NULL`
- `repo_url TEXT`
- `local_path TEXT NOT NULL`
- `subfolder_path TEXT`
- `commit_hash TEXT`
- `status TEXT NOT NULL`
- `status_message TEXT`
- `progress_percent INTEGER`
- `files_indexed INTEGER DEFAULT 0`
- `total_files INTEGER DEFAULT 0`
- `chunks_indexed INTEGER DEFAULT 0`
- `total_chunks INTEGER DEFAULT 0`
- `current_file TEXT`
- `security_goal TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`files`
- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `file_path TEXT NOT NULL`
- `language TEXT`
- `size_bytes INTEGER`
- `line_count INTEGER`
- `is_indexed INTEGER DEFAULT 0`
- `created_at TEXT NOT NULL`

`code_chunks`
- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `file_id TEXT NOT NULL`
- `chunk_type TEXT NOT NULL`
- `symbol_name TEXT`
- `class_name TEXT`
- `start_line INTEGER NOT NULL`
- `end_line INTEGER NOT NULL`
- `code TEXT NOT NULL`
- `security_tags TEXT`
- `embedding_id TEXT`
- `created_at TEXT NOT NULL`

`wiki_pages`
- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `module_id TEXT`
- `title TEXT NOT NULL`
- `slug TEXT NOT NULL`
- `content_markdown TEXT NOT NULL`
- `wiki_schema_version TEXT DEFAULT '1.0'`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`chat_sessions`
- `id TEXT PRIMARY KEY`
- `project_id TEXT NOT NULL`
- `module_id TEXT`
- `model_provider TEXT NOT NULL`
- `model_name TEXT NOT NULL`
- `created_at TEXT NOT NULL`

`chat_messages`
- `id TEXT PRIMARY KEY`
- `session_id TEXT NOT NULL`
- `role TEXT NOT NULL`
- `content TEXT NOT NULL`
- `evidence_json TEXT`
- `raw_model_response TEXT`
- `parsed_answer_json TEXT`
- `validation_status TEXT`
- `created_at TEXT NOT NULL`

`evaluations`
- `id TEXT PRIMARY KEY`
- `project_id TEXT`
- `module_path TEXT`
- `question TEXT`
- `chat_message_id TEXT`
- `model_provider TEXT NOT NULL`
- `model_name TEXT NOT NULL`
- `answer_text TEXT`
- `parsed_answer_json TEXT`
- `evidence_json TEXT`
- `wiki_context_json TEXT`
- `validation_status TEXT`
- `correct_file_path INTEGER`
- `correct_code_block INTEGER`
- `explanation_quality INTEGER`
- `completeness INTEGER`
- `usefulness INTEGER`
- `evaluator_comment TEXT`
- `correctness_score INTEGER`
- `evidence_quality_score INTEGER`
- `score_file_path INTEGER`
- `score_code_block INTEGER`
- `score_explanation INTEGER`
- `score_completeness INTEGER`
- `hallucination_flag INTEGER`
- `latency_ms INTEGER`
- `estimated_cost REAL`
- `human_comment TEXT`
- `created_at TEXT NOT NULL`

`verifications`
- `id TEXT PRIMARY KEY`
- `target_type TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `verdict TEXT NOT NULL`
- `human_comment TEXT`
- `created_at TEXT NOT NULL`

Additional schema observations:
- `wiki_schema_version` exists in `wiki_pages`.
- There is no column named `wiki_content`; wiki content is stored in `wiki_pages.content_markdown`.
- `content_markdown` stores structured JSON for newly generated wikis according to current code.
- `correctness_score`, `evidence_quality_score`, and `hallucination_flag` exist in `evaluations`.

## Requirements and Dependencies
From `backend/requirements.txt`:

- `tree-sitter==0.23.2`
- `tree-sitter-java==0.23.5`
- `tree-sitter-go==0.23.4`
- `sentence-transformers==3.3.1`
- `chromadb==0.6.3`

Other packages:
- `fastapi==0.115.6`
- `uvicorn[standard]==0.34.0`
- `pydantic==2.10.4`
- `pydantic-settings==2.7.0`
- `python-multipart==0.0.20`
- `python-jose[cryptography]==3.3.0`
- `passlib[bcrypt]==1.7.4`
- `httpx==0.27.2`
- `pytest==8.3.3`

Unexpected packages:
- None obvious for the current stated stack. The tree-sitter packages, ChromaDB, and sentence-transformers are expected based on recent project phases.

## Questions I Could Not Answer From Code Alone
Q1: Which `.env` file and absolute storage paths will be used for the thesis demo machine?
Why I need this: The code defines defaults, but runtime environment variables may override SQLite, Chroma, project, and export paths.
Impact if unknown: The demo may read from or write to a different database/vector store than expected.

Q2: Is the semantic embedding model `BAAI/bge-small-en-v1.5` already cached locally on the thesis evaluation machine?
Why I need this: The code loads sentence-transformers with local cache behavior and falls back if the model cannot load.
Impact if unknown: Retrieval quality may silently degrade to hash fallback outside this machine.

Q3: Which LLM provider and model should be considered the official thesis demo baseline?
Why I need this: The code supports Ollama, OpenAI, Gemini, and DeepSeek.
Impact if unknown: Model comparison and final screenshots may not be reproducible.

Q4: Are the existing rows in `backend/storage/security_codewiki.db` real thesis data or disposable development data?
Why I need this: The code can list and export existing projects, but source intent is not encoded in the database.
Impact if unknown: Cleanup or demo preparation could accidentally remove useful case-study data.

Q5: Should old raw-Markdown wiki rows be migrated to structured JSON, or should backward compatibility remain enough?
Why I need this: The code can read old raw text, but no migration policy is encoded.
Impact if unknown: Exports may mix old raw wiki content with new structured wiki content.

Q6: Should the test cleanup issue with `backend/.pytest_runtime` be solved by deleting the locked folder, changing the pytest temp path, or closing a process that holds it?
Why I need this: The test failure is environmental/fixture cleanup related, not an assertion failure.
Impact if unknown: Full-suite testing remains blocked on Windows.

Q7: What are the final human evaluation rubric labels to show in the thesis report?
Why I need this: The database supports both older fields and newer rubric fields.
Impact if unknown: Exported CSV/Markdown may include technically correct but thesis-inconsistent wording.

Q8: Are cloud API keys configured on the final demo machine?
Why I need this: The model health endpoint only reports whether keys are configured at runtime.
Impact if unknown: Cloud provider readiness cannot be guaranteed from source code.

Q9: Which curated case-study packages should be imported before the final demo?
Why I need this: The Android Account Manager package exists, but the code does not say whether it is the only demo dataset.
Impact if unknown: The final thesis demo may lack consistent project data and expected evidence cards.

Q10: Should the frontend project removal button be considered part of the thesis-ready MVP scope?
Why I need this: The user requested project deletion earlier, but this audit was read-only and did not inspect a completed implementation for that feature.
Impact if unknown: The project list may still require manual DB/storage cleanup instead of UI cleanup.
