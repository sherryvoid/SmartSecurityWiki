import pytest


@pytest.mark.parametrize(
    "question",
    [
        "How does the system distinguish an administrator from an ordinary workspace participant? What makes the administrator privileged?",
        "How are a coordinator's permissions distinguished from those of a contributor?",
        "How does the system determine whether someone has a privileged role rather than an ordinary role?",
        "How does the service check whether a principal has elevated privileges instead of standard access?",
    ],
)
def test_privileged_role_comparisons_request_authority_evidence(question):
    from app.services.project_service import _extract_evidence_roles

    assert "needs_authority_checks" in _extract_evidence_roles(question)


@pytest.mark.parametrize(
    "question",
    [
        "What role does this class play in serialization?",
        "Explain the role of this helper function.",
        "What is the role of caching?",
        "What role does the parser play?",
    ],
)
def test_non_security_role_questions_do_not_request_privilege_evidence(question):
    from app.services.project_service import _extract_evidence_roles

    roles = _extract_evidence_roles(question)
    assert "needs_authority_checks" not in roles
    assert "needs_user_assignments" not in roles


def test_unrelated_business_creation_does_not_request_role_assignment():
    from app.services.project_service import _extract_evidence_roles

    question = "Trace the authorized product operation and its concrete product-creation logic."
    assert "needs_user_assignments" not in _extract_evidence_roles(question)


@pytest.mark.parametrize(
    "question",
    [
        "What source establishes the administrator's privileged role?",
        "Where is the coordinator role assigned when the workspace is created, and why is it privileged over a contributor?",
        "How is the initial privileged participant bootstrapped and persisted?",
    ],
)
def test_privileged_role_establishment_requests_assignment_and_authority(question):
    from app.services.project_service import _extract_evidence_roles

    roles = _extract_evidence_roles(question)
    assert {"needs_authority_checks", "needs_user_assignments"}.issubset(roles)


def _item(path, code):
    return {"file_path": path, "symbol_name": "createWorkspace", "start_line": 1, "security_tags": "potential_access_check", "code_snippet": code}


@pytest.mark.parametrize(
    "code",
    [
        'store.set(identityRef(currentUser.uid), mapOf("role" to PRIVILEGED))',
        'Membership(userId = currentPrincipal.id, role = COORDINATOR)',
        'records.insert({"principal_id": subject.id, "authority": elevatedLevel})',
    ],
)
def test_concrete_identity_scoped_role_assignment_satisfies_user_assignments(code):
    from app.services.project_service import _classify_evidence_roles

    assert "needs_user_assignments" in _classify_evidence_roles(_item("src/WorkspaceRepository.kt", code))


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("README.md", "Administrators can invite users."),
        ("res/values/strings.xml", '<string name="admin_only">Only administrators can invite users</string>'),
        ("src/WorkspaceRepository.kt", "// assign administrator role here"),
        ("docs/requirements.txt", "The coordinator role is assigned when a workspace is created."),
        ("src/WorkspaceRepository.kt", 'store.set(workspaceRef, mapOf("role" to PRIVILEGED))'),
        ("src/WorkspaceRepository.kt", "store.set(identityRef(currentUser.uid), profile)"),
    ],
)
def test_prose_comments_and_incomplete_shapes_do_not_satisfy_user_assignments(path, code):
    from app.services.project_service import _classify_evidence_roles

    assert "needs_user_assignments" not in _classify_evidence_roles(_item(path, code))


@pytest.mark.parametrize("privileged,ordinary", [("administrator", "participant"), ("coordinator", "contributor")])
def test_role_coverage_preserves_policy_and_concrete_bootstrap_over_prose(privileged, ordinary):
    from app.services.project_service import _apply_evidence_role_coverage, _extract_evidence_roles

    question = f"How is a {privileged} distinguished from an ordinary {ordinary}, and what source establishes the {privileged}'s privileged role?"
    requested = _extract_evidence_roles(question)
    prose = [
        {"chunk_id": f"prose-{index}", "file_path": "README.md" if index % 2 else "res/values/strings.xml", "symbol_name": None, "start_line": index + 1, "code_snippet": f"{privileged}s can invite {ordinary}s.", "final_score": 1 - index / 100}
        for index in range(4)
    ]
    policy = {"chunk_id": "policy", "file_path": "config/policy.conf", "symbol_name": None, "start_line": 1, "code_snippet": f"permit read, update when hasPrivilege(principal) && role == '{privileged}'", "final_score": .1}
    bootstrap = {"chunk_id": "bootstrap", **_item("src/WorkspaceRepository.kt", f'store.set(identityRef(currentUser.uid), mapOf("role" to "{privileged}"))'), "final_score": .09}

    final = _apply_evidence_role_coverage(prose[:2], [*prose, policy, bootstrap], requested, top_k=2)

    assert requested == {"needs_authority_checks", "needs_user_assignments"}
    assert {item["chunk_id"] for item in final} == {"policy", "bootstrap"}
