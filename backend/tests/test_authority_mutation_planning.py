from app.services.project_service import _extract_evidence_roles


def test_generic_authority_mutation_questions_request_authority_checks_only():
    questions = [
        "Can a recipient override the assigned role while accepting a workspace approval?",
        "Can a user change the privilege they were assigned while activating a registration token?",
        "Can an account choose a different access level while completing enrollment?",
        "Can a principal replace the permission granted by an approval while accepting it?",
        "Can a recipient modify the assigned entitlement while completing an approval workflow?",
    ]
    for question in questions:
        roles = _extract_evidence_roles(question)
        assert "needs_authority_checks" in roles


def test_protected_field_integrity_questions_use_same_authority_role():
    questions = [
        "Can a recipient mark an approval accepted while changing the assigned role?",
        "Can a user activate a token while replacing the assigned permission?",
    ]
    for question in questions:
        roles = _extract_evidence_roles(question)
        assert "needs_authority_checks" in roles


def test_q4_mutation_intent_does_not_automatically_request_user_assignments():
    roles = _extract_evidence_roles(
        "Can an invited user change the role they were invited with while accepting an approval?"
    )
    assert "needs_authority_checks" in roles
    assert "needs_user_assignments" not in roles


def test_ordinary_mutations_do_not_request_authority_checks():
    questions = [
        "Can a user change their display name while editing their profile?",
        "Can a user change the application theme in settings?",
        "Can a parser modify its internal buffer size during parsing?",
        "Can a callback replace a temporary label during execution?",
        "Can a user change the difficulty level in game settings?",
        "Can the system change a logging scope during startup?",
    ]
    assert all("needs_authority_checks" not in _extract_evidence_roles(question) for question in questions)


def test_single_authority_mutation_words_do_not_overtrigger():
    questions = [
        "Can a user change this value?",
        "Can a role appear in documentation?",
        "Can the level increase during processing?",
        "Can a user inspect a scope?",
    ]
    assert all("needs_authority_checks" not in _extract_evidence_roles(question) for question in questions)
