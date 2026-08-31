from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "manifest_component_retrieval"


@pytest.mark.parametrize(
    "question",
    [
        "Which Android components are exported?",
        "Which exported components are protected by a component-level permission?",
        "List all exported Android activities, services, receivers, and providers.",
        "What components in the Android manifest are externally accessible?",
        "Which receivers and services are exported?",
    ],
)
def test_manifest_component_list_questions_enumerate(question):
    from app.services.project_service import _has_enumeration_intent

    assert _has_enumeration_intent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "What is an Android component?",
        "How does android:exported work?",
        "Is MainActivity exported?",
        "Does this service require a permission?",
        "Explain Android component permissions.",
        "What does android:permission mean?",
    ],
)
def test_single_or_conceptual_manifest_questions_do_not_enumerate(question):
    from app.services.project_service import _has_enumeration_intent

    assert _has_enumeration_intent(question) is False


def _item(symbol, code, chunk_type="xml_component", manifest_intent=True):
    return {
        "chunk_id": symbol,
        "file_path": "app/src/main/AndroidManifest.xml",
        "symbol_name": symbol,
        "chunk_type": chunk_type,
        "security_tags": "",
        "code_snippet": code,
        "manifest_component_intent": manifest_intent,
    }


def test_manifest_components_reuse_declaration_and_authority_roles_narrowly():
    from app.services.project_service import _classify_evidence_roles

    open_activity = _item("activity:.OpenActivity", '<activity android:exported="true" />')
    protected_service = _item("service:.ProtectedService", '<service android:permission="example.BIND" />')
    protected_provider = _item("provider:.DataProvider", '<provider android:readPermission="example.READ" android:writePermission="example.WRITE" />')
    unrelated_context = _item("service:.ProtectedService", '<service android:permission="example.BIND" />', manifest_intent=False)
    uses_permission = _item("uses-permission:INTERNET", '<uses-permission android:name="android.permission.INTERNET" />', "xml_permission")
    metadata = _item("meta-data:widget", '<meta-data android:name="android.appwidget.provider" />', "xml_element")
    intent_filter = _item("intent-filter", '<intent-filter><action android:name="example.ACTION" /></intent-filter>', "xml_element")

    assert "needs_endpoint_declarations" in _classify_evidence_roles(open_activity)
    assert "needs_authority_checks" not in _classify_evidence_roles(open_activity)
    assert {"needs_endpoint_declarations", "needs_authority_checks"}.issubset(_classify_evidence_roles(protected_service))
    assert {"needs_endpoint_declarations", "needs_authority_checks"}.issubset(_classify_evidence_roles(protected_provider))
    assert "needs_endpoint_declarations" not in _classify_evidence_roles(unrelated_context)
    for item in (uses_permission, metadata, intent_filter):
        roles = _classify_evidence_roles(item)
        assert "needs_endpoint_declarations" not in roles
        assert "needs_authority_checks" not in roles


def _insert_fixture(connection, project_id, manifest_text):
    from app.services.parser import chunk_source

    files = (("manifest-file", "app/src/main/AndroidManifest.xml", "xml"), ("source-file", "src/Unrelated.kt", "kotlin"))
    for file_id, path, language in files:
        connection.execute(
            "INSERT INTO files (id,project_id,file_path,language,size_bytes,line_count,is_indexed,created_at) VALUES (?,?,?,?,100,30,1,'now')",
            (file_id, project_id, path, language),
        )
    for index, chunk in enumerate(chunk_source("app/src/main/AndroidManifest.xml", "xml", manifest_text)):
        connection.execute(
            "INSERT INTO code_chunks (id,project_id,file_id,chunk_type,symbol_name,class_name,start_line,end_line,code,security_tags,embedding_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'now')",
            (f"manifest-{index}", project_id, "manifest-file", chunk.chunk_type, chunk.symbol, None, chunk.start_line, chunk.end_line, chunk.content, ",".join(chunk.tags), f"code:manifest-{index}"),
        )
    connection.execute(
        "INSERT INTO code_chunks (id,project_id,file_id,chunk_type,symbol_name,class_name,start_line,end_line,code,security_tags,embedding_id,created_at) VALUES ('noise',?,'source-file','class','Unrelated','Unrelated',1,5,?,'potential_access_check','code:noise','now')",
        (project_id, (FIXTURE_ROOT / "Unrelated.kt").read_text(encoding="utf-8")),
    )


def test_synthetic_manifest_completeness_exceeds_top_k_and_preserves_roles(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "manifest-completeness"
    manifest = (FIXTURE_ROOT / "AndroidManifest.xml").read_text(encoding="utf-8")
    with db() as connection:
        _insert_fixture(connection, project_id, manifest)
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = "Which Android components are externally exposed, and which exported components are protected by component-level permissions?"
    with db() as connection:
        package = project_service.retrieve_evidence_package(project_id, question, 3, connection)

    components = [item for item in package["source_chunks"] if item["chunk_type"] == "xml_component"]
    symbols = {item["symbol_name"] for item in components}
    assert symbols == {
        "activity:.OpenActivity", "service:.ProtectedService", "receiver:.InternalReceiver",
        "provider:.ExportedProvider", "activity-alias:.OpenAlias",
    }
    assert len(components) == 5 > 3
    assert "noise" not in {item["chunk_id"] for item in package["source_chunks"]}
    diagnostics = package["diagnostics"]
    assert diagnostics["enumeration_intent"] is True
    assert diagnostics["manifest_component_intent"] is True
    assert diagnostics["manifest_component_completeness"]["candidate_count"] == 5
    assert {"needs_endpoint_declarations", "needs_authority_checks"}.issubset(diagnostics["satisfied_evidence_roles"])
    assert "uses-permission:android.permission.INTERNET" not in {
        item["symbol_name"] for item in components
    }


@pytest.mark.parametrize(
    "manifest,expected",
    [
        ('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application /></manifest>', set()),
        ('<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application><receiver android:name=".One" android:exported="false"/><service android:name=".Two" android:exported="false"/></application></manifest>', {"receiver:.One", "service:.Two"}),
    ],
)
def test_manifest_completeness_handles_zero_and_only_non_exported_components(isolated_env, monkeypatch, manifest, expected):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "manifest-edge"
    with db() as connection:
        _insert_fixture(connection, project_id, manifest)
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    with db() as connection:
        package = project_service.retrieve_evidence_package(project_id, "Which Android components are exported?", 2, connection)
    assert {item["symbol_name"] for item in package["source_chunks"] if item["chunk_type"] == "xml_component"} == expected


def test_manifest_and_binder_query_expansion_are_kept_separate():
    from app.services.project_service import _has_enumeration_intent
    from app.services.vector_index import expand_security_query

    manifest = expand_security_query("Which Android components are exported and protected by component permissions?")
    binder = expand_security_query("How does this Binder service enforce caller permission?")

    assert "AndroidManifest" in manifest and "android:exported" in manifest and "readPermission" in manifest
    assert not any(term in manifest for term in ("checkPermission", "enforcePermission", "Binder.getCallingUid"))
    assert all(term in binder for term in ("checkPermission", "enforcePermission", "Binder.getCallingUid"))
    assert _has_enumeration_intent("How does this Binder service enforce caller permission?") is False


def test_completeness_stays_with_highest_ranked_manifest_scope():
    from app.services.project_service import _apply_manifest_component_completeness

    ranked = [
        {**_item("activity:.Primary", '<activity android:exported="true" />'), "file_path": "mobile/src/main/AndroidManifest.xml", "start_line": 2},
        {**_item("service:.PrimaryService", '<service android:exported="true" />'), "file_path": "mobile/src/main/AndroidManifest.xml", "start_line": 3},
        {**_item("activity:.Secondary", '<activity android:exported="true" />'), "file_path": "wear/src/main/AndroidManifest.xml", "start_line": 2},
    ]
    result, diagnostics = _apply_manifest_component_completeness([], ranked, True, True, None, 10)

    assert {item["symbol_name"] for item in result} == {"activity:.Primary", "service:.PrimaryService"}
    assert diagnostics["scope"] == "mobile/src/main/androidmanifest.xml"
