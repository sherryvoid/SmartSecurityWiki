from app.services.project_service import _pack_overlapping_evidence


def _chunk(chunk_id, symbol, start, end, score, signals, chunk_type="function"):
    return {
        "chunk_id": chunk_id,
        "id": chunk_id,
        "file_path": "src/NeutralService.kt",
        "symbol_name": symbol,
        "start_line": start,
        "end_line": end,
        "chunk_type": chunk_type,
        "final_score": score,
        "lexical_matches": list(signals),
        "exact_identifier_matches": [],
        "security_tags": "",
        "code_snippet": symbol,
        "http_method": "",
    }


def test_initialize_child_can_replace_bootstrap_parent_but_join_sibling_cannot():
    parent = _chunk("parent", "WorkspaceService", 1, 100, 1.0, ("bootstrap", "workspace", "create", "administrator"), "class")
    initialize = _chunk("initialize", "initializeWorkspace", 10, 35, .9, ("bootstrap", "workspace", "create", "administrator"))
    join = _chunk("join", "joinWorkspace", 40, 60, .95, ("workspace", "administrator"))
    packed, diagnostics = _pack_overlapping_evidence([parent], [parent, join, initialize], set(), 10)
    ids = {item["chunk_id"] for item in packed}
    assert "initialize" in ids
    assert "join" not in ids
    assert diagnostics["parent_child_replacements"][0]["child_chunk_ids"] == ["initialize"]


def test_account_creation_child_wins_over_existing_member_sibling():
    parent = _chunk("parent", "AccountService", 1, 100, 1.0, ("create", "account", "first", "owner"), "class")
    create = _chunk("create", "createAccountWithInitialOwner", 10, 40, .9, ("create", "account", "first", "owner"))
    add = _chunk("add", "addExistingMember", 45, 70, .95, ("account", "owner"))
    packed, _ = _pack_overlapping_evidence([parent], [parent, add, create], set(), 10)
    assert {item["chunk_id"] for item in packed} == {"create"}


def test_project_initialization_child_wins_over_update_and_archive_siblings():
    parent = _chunk("parent", "ProjectService", 1, 100, 1.0, ("setup", "project", "atomic"), "class")
    initialize = _chunk("initialize", "initializeProject", 10, 35, .9, ("setup", "project", "atomic"))
    update = _chunk("update", "updateProject", 40, 60, .95, ("project", "atomic"))
    archive = _chunk("archive", "archiveProject", 65, 85, .95, ("project", "atomic"))
    packed, _ = _pack_overlapping_evidence([parent], [parent, update, archive, initialize], set(), 10)
    assert {item["chunk_id"] for item in packed} == {"initialize"}


def test_unrelated_join_child_does_not_replace_bootstrap_parent_when_no_good_child_exists():
    parent = _chunk("parent", "WorkspaceService", 1, 100, 1.0, ("bootstrap", "workspace", "first", "administrator"), "class")
    join = _chunk("join", "joinWorkspace", 40, 60, .95, ("workspace", "administrator"))
    packed, diagnostics = _pack_overlapping_evidence([parent], [parent, join], set(), 10)
    assert {item["chunk_id"] for item in packed} == {"parent"}
    assert diagnostics["parent_child_replacement_count"] == 0
