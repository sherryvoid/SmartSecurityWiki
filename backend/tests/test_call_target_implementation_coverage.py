from app.db.database import db, init_db


TRACE_QUERY = (
    "Requirement trace: identify the protected operation, direct downstream implementation call, "
    "and source boundary. Separate authorization from business implementation."
)


def _insert_project(project_id, files, chunks):
    with db() as connection:
        for file_id, path in files:
            connection.execute(
                """INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
                   VALUES (?, ?, ?, 'java', 100, 30, 1, 'now')""", (file_id, project_id, path),
            )
        for chunk_id, file_id, symbol, class_name, code, tags in chunks:
            connection.execute(
                """INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name,
                   start_line, end_line, code, security_tags, embedding_id, created_at)
                   VALUES (?, ?, ?, 'method', ?, ?, 5, 12, ?, ?, ?, 'now')""",
                (chunk_id, project_id, file_id, symbol, class_name, code, tags, f"code:{chunk_id}"),
            )


def _retrieve(project_id, monkeypatch, top_k=4):
    from app.services import project_service

    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    with db() as connection:
        return project_service.retrieve_evidence_package(project_id, TRACE_QUERY, top_k, connection)


def test_resolved_direct_call_supplies_concrete_implementation(isolated_env, monkeypatch):
    init_db()
    _insert_project("orders", [("handler", "src/OrderHandler.java"), ("engine", "src/OrderEngine.java")], [
        ("cancel-entry", "handler", "cancel", "OrderHandler", "OrderEngine orderEngine; @DeleteMapping @PreAuthorize(\"hasAuthority('OPS')\") void cancel(long id) { orderEngine.cancelOrder(id); }", "deletemapping,preauthorize"),
        ("cancel-impl", "engine", "cancelOrder", "OrderEngine", "void cancelOrder(long id) { ledger.remove(id); }", "service"),
    ])

    package = _retrieve("orders", monkeypatch)

    assert "cancel-impl" in {item["chunk_id"] for item in package["source_chunks"]}
    assert "needs_helper_implementation" in package["diagnostics"]["satisfied_evidence_roles"]
    assert package["diagnostics"]["direct_downstream_calls"][0]["target_resolution"] == "found"
    assert package["diagnostics"]["direct_downstream_calls"][0]["implementation_chunk_id"] == "cancel-impl"
    assert package["diagnostics"]["evidence_role_by_chunk"]["cancel-impl"].count("needs_helper_implementation") == 1


def test_unresolved_call_is_not_closed_by_docs_tests_or_config(isolated_env, monkeypatch):
    init_db()
    _insert_project("audit", [("handler", "src/AuditHandler.java"), ("noise", "docs/README.java"), ("test", "test/AuditTest.java")], [
        ("publish-entry", "handler", "publish", "AuditHandler", "AuditClient auditClient; @PostMapping @PreAuthorize(\"hasAuthority('AUDITOR')\") void publish(Object record) { auditClient.publish(record); }", "postmapping,preauthorize"),
        ("readme", "noise", "serviceNotes", "Readme", "class ServiceDocumentation { void helper() {} }", "service,permission"),
        ("test-helper", "test", "publishTest", "AuditTest", "void publishTest() { helperService.verify(); }", "service,filter"),
    ])

    package = _retrieve("audit", monkeypatch)

    assert "needs_helper_implementation" in package["diagnostics"]["unsatisfied_evidence_roles"]
    assert package["diagnostics"]["direct_downstream_calls"][0]["target_resolution"] == "not_found"
    assert all("needs_helper_implementation" not in roles for roles in package["diagnostics"]["evidence_role_by_chunk"].values())


def test_owner_type_disambiguates_same_named_methods(isolated_env, monkeypatch):
    init_db()
    _insert_project("dispatch", [("handler", "src/DispatchHandler.java"), ("primary", "src/DispatchEngine.java"), ("other", "src/ArchiveEngine.java")], [
        ("dispatch-entry", "handler", "dispatch", "DispatchHandler", "DispatchEngine dispatchEngine; @PostMapping @PreAuthorize(\"hasAuthority('AGENT')\") void dispatch(long id) { dispatchEngine.execute(id); }", "postmapping,preauthorize"),
        ("right-execute", "primary", "execute", "DispatchEngine", "void execute(long id) { queue.submit(id); }", "service"),
        ("wrong-execute", "other", "execute", "ArchiveEngine", "void execute(long id) { archive.store(id); }", "service"),
    ])

    package = _retrieve("dispatch", monkeypatch, top_k=2)
    call = package["diagnostics"]["direct_downstream_calls"][0]

    assert call["target_resolution"] == "found"
    assert call["implementation_chunk_id"] == "right-execute"
    assert "right-execute" in {item["chunk_id"] for item in package["source_chunks"]}
    assert "wrong-execute" not in {item["chunk_id"] for item in package["source_chunks"]}
