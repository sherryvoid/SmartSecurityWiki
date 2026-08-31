from app.services.project_service import (
    _extract_evidence_roles,
    _pack_overlapping_evidence,
)


def _chunk(chunk_id, path, start, end, code, tags="potential_access_check"):
    return {
        "chunk_id": chunk_id,
        "file_path": path,
        "start_line": start,
        "end_line": end,
        "chunk_type": "line_range_fallback",
        "code_snippet": code,
        "security_tags": tags,
        "retrieval_rank": start,
        "final_score": 0.5,
    }


def test_generic_actor_operation_prerequisite_questions_request_authority():
    questions = [
        "Can a user join a workspace without pending approval?",
        "Can a caller register this protected device without a valid token?",
        "Can an account activate the feature without the required entitlement?",
        "May a principal create membership unless approval already exists?",
        "Is operation X allowed only if condition Y exists?",
        "What condition must be satisfied before this user may perform this action?",
        "Must there already be an approved pending request before a user joins a workspace, and what prevents a direct membership write?",
        "Trace the authorization path and explain what prevents a caller from creating their own protected enrollment without prior approval.",
    ]
    assert all("needs_authority_checks" in _extract_evidence_roles(question) for question in questions)


def test_ordinary_technical_prerequisites_do_not_request_authority():
    questions = [
        "Can the parser continue without a closing token?",
        "Can the build run without a license header file?",
        "Can this callback be registered without an optional label?",
        "Can serialization work without this helper?",
        "Can this function execute without caching enabled?",
        "A pending parser task joins the route table before rendering.",
        "Explain how a member joins two path segments in this helper.",
        "What prevents this formatter from creating an optional label?",
    ]
    assert all("needs_authority_checks" not in _extract_evidence_roles(question) for question in questions)


def test_split_policy_continuation_is_preserved():
    first = _chunk("policy-1", "config/policy.conf", 1, 80, "allow create: if caller.id == target.id\n    && hasPendingApproval(")
    second = _chunk("policy-2", "config/policy.conf", 81, 120, ")\n    && requested.role == approval.role;")
    packed, diagnostics = _pack_overlapping_evidence([first], [first, second], {"needs_authority_checks"}, 2)
    assert [item["chunk_id"] for item in packed] == ["policy-1", "policy-2"]
    assert diagnostics["split_policy_continuation_count"] == 1


def test_complete_policy_does_not_pull_neighbor():
    first = _chunk("complete", "config/policy.conf", 1, 80, "allow create: if caller.id == target.id;")
    neighbor = _chunk("neighbor", "config/policy.conf", 81, 120, "&& unrelated();")
    packed, diagnostics = _pack_overlapping_evidence([first], [first, neighbor], {"needs_authority_checks"}, 2)
    assert [item["chunk_id"] for item in packed] == ["complete"]
    assert diagnostics["split_policy_continuation_count"] == 0


def test_nonadjacent_and_different_file_chunks_are_not_continuations():
    first = _chunk("first", "config/policy.conf", 1, 80, "allow create: if caller.id == target.id\n    && pending(")
    nonadjacent = _chunk("gap", "config/policy.conf", 82, 120, ") && approved();")
    other_file = _chunk("other", "config/other.conf", 81, 120, ") && approved();")
    packed, diagnostics = _pack_overlapping_evidence([first], [first, nonadjacent, other_file], {"needs_authority_checks"}, 3)
    assert [item["chunk_id"] for item in packed] == ["first"]
    assert diagnostics["split_policy_continuation_count"] == 0


def test_split_policy_continuation_replaces_only_redundant_requested_role_evidence():
    first = _chunk("policy-1", "config/policy.conf", 1, 80, "allow create: if caller.id == target.id\n    && approved(")
    continuation = _chunk("policy-2", "config/policy.conf", 81, 120, ")\n    && requested.level == approval.level;")
    helper_a = _chunk("helper-a", "src/service.py", 1, 20, "class WorkspaceService: pass", "service")
    helper_b = _chunk("helper-b", "src/repository.py", 1, 20, "class WorkspaceRepository: pass", "repository")
    packed, diagnostics = _pack_overlapping_evidence(
        [first, helper_a, helper_b], [first, continuation, helper_a, helper_b],
        {"needs_authority_checks", "needs_helper_implementation"}, 3,
    )
    ids = [item["chunk_id"] for item in packed]
    assert {"policy-1", "policy-2"}.issubset(ids)
    assert len({"helper-a", "helper-b"}.intersection(ids)) == 1
    assert diagnostics["split_policy_continuation_count"] == 1
