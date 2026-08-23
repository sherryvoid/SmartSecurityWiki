def _insert_fixture(connection, project_id: str, with_hierarchy: bool):
    files = [(f"{project_id}-main", "src/gateway.py"), (f"{project_id}-rules", "lib/relations.py")]
    for file_id, path in files:
        connection.execute(
            """INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
               VALUES (?, ?, ?, 'python', 100, 20, 1, 'now')""",
            (file_id, project_id, path),
        )
    chunks = [
        (f"{project_id}-guard", files[0][0], "require_access", "@permission_required('record:read')\ndef read_record(): pass", "authorization"),
        (f"{project_id}-plain", files[1][0], "load_rules", "def load_rules(): return {'record:read'}", "authorization"),
    ]
    if with_hierarchy:
        chunks[1] = (
            f"{project_id}-hierarchy", files[1][0], "build_policy_relations",
            "permission_inheritance = ParentChildMapping(parent='record:admin', child='record:read')",
            "authorization",
        )
    for chunk_id, file_id, symbol, code, tags in chunks:
        connection.execute(
            """INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
               VALUES (?, ?, ?, 'function', ?, 'Fixture', 1, 10, ?, ?, ?, 'now')""",
            (chunk_id, project_id, file_id, symbol, code, tags, f"code:{chunk_id}"),
        )


def test_generic_repository_existence_search_finds_hierarchy_outside_main_config(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    with db() as connection:
        _insert_fixture(connection, "positive-existence", True)
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = "Does this codebase define permission inheritance anywhere?"
    with db() as connection:
        package = project_service.retrieve_evidence_package("positive-existence", question, 5, connection)

    search = package["diagnostics"]["repository_existence_searches"][0]
    assert search["concept_searched"] == "permission inheritance"
    assert search["existence_result"] == "found"
    assert search["candidate_count"] == 1
    assert search["lexical_hits"][0]["file_path"] == "lib/relations.py"


def test_generic_repository_existence_search_reports_not_found_without_fake_evidence(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    with db() as connection:
        _insert_fixture(connection, "negative-existence", False)
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = "Is there any permission inheritance in this repository?"
    with db() as connection:
        package = project_service.retrieve_evidence_package("negative-existence", question, 5, connection)

    search = package["diagnostics"]["repository_existence_searches"][0]
    assert search["existence_result"] == "not_found"
    assert search["candidate_count"] == 0
    assert search["exact_symbol_hits"] == []
    assert search["lexical_hits"] == []
    assert all("not found" not in item["code_snippet"].lower() for item in package["source_chunks"])


def test_non_existence_question_does_not_run_extra_repository_search(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    with db() as connection:
        _insert_fixture(connection, "normal-question", True)
    calls = []
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: calls.append(args[1]) or [])
    with db() as connection:
        package = project_service.retrieve_evidence_package("normal-question", "Explain the permission check", 5, connection)
    assert len(calls) == 2  # Normal source retrieval plus the existing Wiki lookup; no concept-specific scan.
    assert calls == ["Explain the permission check", "Explain the permission check"]
    assert package["diagnostics"]["repository_concept_existence_intent"] is False
    assert package["diagnostics"]["repository_existence_searches"] == []


def test_q7_wording_extracts_repository_wide_hierarchy_concept():
    from app.services.project_service import _extract_repository_existence_concepts

    question = (
        "Does this application use hasRole, hasAuthority, or both for access control? Explain how authority names are stored and checked, "
        "whether Spring's usual ROLE_ prefix is added anywhere, and whether the repository defines any role hierarchy or inheritance "
        "between authorities such as STAFF_MEMBER, ASSISTANT_MANAGER, MANAGER, and ADMIN."
    )
    concepts = _extract_repository_existence_concepts(question)
    assert any("role hierarchy or inheritance between authorities" == concept.lower() for concept in concepts)


def test_positive_and_negative_existence_metadata_serialize_without_fake_source_blocks():
    from app.services.project_service import EXISTENCE_SEARCH_LIMITATION, repository_existence_to_prompt

    found = [{
        "concept_searched": "permission inheritance", "search_terms": ["permission inheritance", "PermissionInheritance"],
        "search_scope": "all indexed source chunks in repository", "scanned_chunk_count": 2, "candidate_count": 1,
        "exact_symbol_hits": [], "lexical_hits": [{"chunk_id": "real-source", "file_path": "lib/relations.py", "symbol_name": "build_policy_relations"}],
        "semantic_hits": [], "existence_result": "found",
    }]
    negative = [{
        "concept_searched": "permission inheritance", "search_terms": ["permission inheritance"],
        "search_scope": "all indexed source chunks in repository", "scanned_chunk_count": 2, "candidate_count": 0,
        "exact_symbol_hits": [], "lexical_hits": [], "semantic_hits": [], "existence_result": "not_found",
    }]
    found_text = repository_existence_to_prompt(found)
    negative_text = repository_existence_to_prompt(negative)
    assert "[X1] Repository-wide existence check" in found_text
    assert "RESULT: found" in found_text and '"chunk_id":"real-source"' in found_text
    assert "RESULT: not_found" in negative_text
    assert "MATCHING SOURCE REFERENCES: []" in negative_text
    assert EXISTENCE_SEARCH_LIMITATION in negative_text


def test_uncertain_existence_metadata_stays_uncertain():
    from app.services.project_service import repository_existence_to_prompt

    searches = [{
        "concept_searched": "access level graph", "search_terms": ["access level graph"],
        "search_scope": "all indexed source chunks in repository", "scanned_chunk_count": 4, "candidate_count": 1,
        "exact_symbol_hits": [], "lexical_hits": [],
        "semantic_hits": [{"chunk_id": "possible", "file_path": "rules/graph.py", "symbol_name": "connect", "similarity": 0.7}],
        "existence_result": "uncertain",
    }]
    serialized = repository_existence_to_prompt(searches)
    assert "RESULT: uncertain" in serialized
    assert "Possible semantic matches" in serialized
    assert "RESULT: not_found" not in serialized


def test_compare_package_hash_includes_existence_metadata():
    from app.services.audit_service import _freeze_shared_evidence_package

    source = [{"chunk_id": "same", "file_path": "src/a.py", "start_line": 1, "end_line": 2, "code_snippet": "allow()"}]
    base = {"concept_searched": "policy inheritance", "search_terms": ["policy inheritance"], "search_scope": "all indexed source chunks in repository", "scanned_chunk_count": 1, "candidate_count": 0, "exact_symbol_hits": [], "lexical_hits": [], "semantic_hits": []}
    not_found = _freeze_shared_evidence_package(source, [{**base, "existence_result": "not_found"}])
    uncertain = _freeze_shared_evidence_package(source, [{**base, "existence_result": "uncertain", "candidate_count": 1, "semantic_hits": [{"chunk_id": "same", "similarity": .7}]}])
    assert not_found["ordered_chunk_ids"] == uncertain["ordered_chunk_ids"] == ["same"]
    assert not_found["shared_evidence_hash"] != uncertain["shared_evidence_hash"]
    assert not_found["shared_evidence_package_id"] != uncertain["shared_evidence_package_id"]


def test_normal_prompt_has_no_existence_section():
    from app.services.audit_service import _chat_messages_for_provider

    provider = type("Provider", (), {"name": "gemini"})()
    source = [{"chunk_id": "e", "file_path": "src/a.py", "symbol_name": "allow", "start_line": 1, "end_line": 1, "code_snippet": "allow()"}]
    content = _chat_messages_for_provider(provider, "Explain access", source, [])[1]["content"]
    assert "REPOSITORY EXISTENCE CHECKS" not in content
    assert "Repository-wide existence metadata" not in content


def test_q12_style_grouped_identifiers_are_extracted_independently():
    from app.services.project_service import _extract_repository_existence_concepts

    question = (
        "Does this application define, assign, check, or authorize any of the following concepts: "
        "SUPER_ADMIN, PRODUCT_OWNER, or CUSTOMER? Evaluate each name separately. "
        "Do not map these names onto ADMIN or MANAGER."
    )
    assert _extract_repository_existence_concepts(question) == ["SUPER_ADMIN", "PRODUCT_OWNER", "CUSTOMER"]


def test_natural_language_existence_intent_variants_and_negative_controls():
    from app.services.project_service import _extract_repository_existence_concepts

    positives = {
        "Does this project define or use ROOT_OPERATOR anywhere in indexed source?": ["ROOT_OPERATOR"],
        "Are BILLING_OWNER and READ_ONLY_GUEST assigned or checked anywhere in the codebase?": ["BILLING_OWNER", "READ_ONLY_GUEST"],
        "Is DEVICE_TRUST absent from this repository?": ["DEVICE_TRUST"],
        "Which of ALPHA_ROLE, BETA_ROLE, and GAMMA_ROLE are actually implemented?": ["ALPHA_ROLE", "BETA_ROLE", "GAMMA_ROLE"],
        "Does the application authorize any concept named TENANT_SUPERVISOR?": ["TENANT_SUPERVISOR"],
    }
    for question, expected in positives.items():
        assert _extract_repository_existence_concepts(question) == expected

    for question in (
        "How is ADMIN authorized on the delete endpoint?",
        "Explain the JWT roles claim.",
        "Trace ProductService.deleteProductById.",
        "Can STAFF_MEMBER add or delete products? Use the actual authorization expressions from the repository.",
        "Explain how JWTs are created and how incoming JWTs are validated from repository evidence.",
        "Is this application explicitly configured for stateless session handling? Identify the exact configuration.",
        "Separate request-level authentication from method-level authorization in this application.",
    ):
        assert _extract_repository_existence_concepts(question) == []


def test_mixed_results_keep_scope_and_avoid_longer_identifier_false_positive(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "mixed-identifiers"
    with db() as connection:
        files = (("prod", "src/policy.py"), ("docs", "docs/access.md"), ("tests", "tests/test_policy.py"))
        for file_id, path in files:
            connection.execute(
                """INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
                   VALUES (?, ?, ?, 'python', 100, 20, 1, 'now')""", (file_id, project_id, path),
            )
        chunks = (
            ("alpha", "prod", "policy", "ALPHA_ADMIN = 'enabled'"),
            ("longer", "prod", "other_policy", "SUPER_ADMINISTRATOR = 'enabled'"),
            ("docs-only", "docs", "notes", "DOCS_VIEWER is described here"),
            ("test-only", "tests", "test_policy", "TEST_AUDITOR = 'fixture'"),
        )
        for chunk_id, file_id, symbol, code in chunks:
            connection.execute(
                """INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
                   VALUES (?, ?, ?, 'function', ?, 'Fixture', 1, 3, ?, '', ?, 'now')""",
                (chunk_id, project_id, file_id, symbol, code, f"code:{chunk_id}"),
            )
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = "Which of ALPHA_ADMIN, BETA_OWNER, SUPER_ADMIN, DOCS_VIEWER, and TEST_AUDITOR are actually implemented?"
    with db() as connection:
        package = project_service.retrieve_evidence_package(project_id, question, 5, connection)

    searches = {item["concept_searched"]: item for item in package["diagnostics"]["repository_existence_searches"]}
    assert searches["ALPHA_ADMIN"]["existence_result"] == "found"
    assert searches["BETA_OWNER"]["existence_result"] == "not_found"
    assert searches["SUPER_ADMIN"]["existence_result"] == "not_found"
    assert searches["DOCS_VIEWER"]["lexical_hits"][0]["source_scope"] == "documentation"
    assert searches["TEST_AUDITOR"]["lexical_hits"][0]["source_scope"] == "test"
