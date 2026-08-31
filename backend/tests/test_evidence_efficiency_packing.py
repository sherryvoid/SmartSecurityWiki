from app.services.project_service import _pack_overlapping_evidence


def _chunk(chunk_id, path, kind, start, end, score, code, roles=(), matches=()):
    security = "preauthorize" if "needs_authority_checks" in roles else ""
    return {
        "chunk_id": chunk_id, "file_path": path, "chunk_type": kind,
        "start_line": start, "end_line": end, "final_score": score,
        "retrieval_rank": int(chunk_id.strip("pcv") or 1), "code_snippet": code,
        "security_tags": security, "lexical_matches": list(matches),
        "exact_identifier_matches": [],
    }


def test_relevant_child_replaces_contained_large_parent():
    parent = _chunk("p1", "src/Policy.kt", "class", 1, 100, 1.0, "@PreAuthorize class Policy { fun allow() }", ("needs_authority_checks",), ("allow",))
    child = _chunk("c1", "src/Policy.kt", "function", 20, 30, .9, "@PreAuthorize fun allow()", ("needs_authority_checks",), ("allow",))
    packed, diagnostics = _pack_overlapping_evidence([parent], [parent, child], {"needs_authority_checks"}, 10)
    assert [item["chunk_id"] for item in packed] == ["c1"]
    assert packed[0]["file_path"] == "src/Policy.kt" and packed[0]["start_line"] == 20 and packed[0]["end_line"] == 30
    assert diagnostics["parent_child_replacement_count"] == 1


def test_two_distinct_children_are_retained_when_both_cover_parent_signals():
    parent = _chunk("p1", "src/Gate.java", "class", 1, 100, 1.0, "@PreAuthorize class Gate { helper service }", ("needs_authority_checks",), ("read", "write"))
    read = _chunk("c1", "src/Gate.java", "method", 10, 20, .9, "@PreAuthorize read", ("needs_authority_checks",), ("read",))
    write = _chunk("c2", "src/Gate.java", "method", 30, 40, .9, "helper service write", (), ("write",))
    packed, _ = _pack_overlapping_evidence([parent], [parent, read, write], {"needs_authority_checks", "needs_helper_implementation"}, 10)
    assert {item["chunk_id"] for item in packed} == {"c1", "c2"}


def test_parent_remains_with_unique_required_evidence_or_irrelevant_child():
    parent = _chunk("p1", "src/Gate.kt", "class", 1, 100, 1.0, "@PreAuthorize class Gate", ("needs_authority_checks",), ("owner", "delete"))
    partial = _chunk("c1", "src/Gate.kt", "function", 10, 20, .9, "owner", ("needs_authority_checks",), ("owner",))
    unrelated = _chunk("c2", "src/Gate.kt", "function", 30, 40, 2.0, "format title", (), ())
    packed, diagnostics = _pack_overlapping_evidence([parent], [parent, partial, unrelated], {"needs_authority_checks"}, 10)
    assert [item["chunk_id"] for item in packed] == ["p1"]
    assert diagnostics["parent_child_replacement_count"] == 0


def test_true_locale_siblings_deduplicate_but_unique_security_variant_survives():
    base = _chunk("v1", "app/src/main/res/values/strings.xml", "file", 1, 5, 1, '<string name="title">Title</string>', (), ("title",))
    de = _chunk("v2", "app/src/main/res/values-de/strings.xml", "file", 1, 5, 1, '<string name="title">Titel</string>', (), ("title",))
    es = _chunk("v3", "app/src/main/res/values-es/strings.xml", "file", 1, 6, 1, '<string name="title">Título</string><string name="admin_access">Administrar</string>', (), ("title",))
    similar = _chunk("v4", "docs/values-fr/strings.xml", "file", 1, 5, 1, '<string name="title">Titre</string>', (), ("title",))
    packed, diagnostics = _pack_overlapping_evidence([base, de, es, similar], [base, de, es, similar], set(), 10)
    assert {item["chunk_id"] for item in packed} == {"v1", "v3", "v4"}
    assert diagnostics["localized_resource_chunks_removed"] == ["v2"]
    assert next(item for item in packed if item["chunk_id"] == "v3")["file_path"].endswith("values-es/strings.xml")
