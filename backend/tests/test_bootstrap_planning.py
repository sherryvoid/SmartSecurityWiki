import pytest

from app.services.project_service import _extract_evidence_roles


@pytest.mark.parametrize(
    "question",
    [
        "How does the system bootstrap a new workspace and establish the first administrator?",
        "How is a tenant initialized with its initial owner?",
        "How does account provisioning create the first privileged member?",
        "How is a project created and its first administrator established safely?",
        "Does workspace setup create the tenant and first admin atomically?",
        "How does initial organization setup prevent an unauthorized user from becoming the first owner?",
        "How does the bootstrap flow create the organization and first admin consistently?",
    ],
)
def test_bootstrap_first_privileged_identity_requests_exact_existing_roles(question):
    assert _extract_evidence_roles(question) == {
        "needs_user_assignments",
        "needs_authority_checks",
    }


@pytest.mark.parametrize(
    "question",
    [
        "How is the owner label translated?",
        "How is the setup screen rendered?",
        "Which file contains onboarding?",
        "How is the application configured?",
        "How is the first screen displayed?",
        "How is the project name created?",
        "How does a user join an existing workspace?",
    ],
)
def test_non_bootstrap_questions_do_not_request_bootstrap_security_roles(question):
    roles = _extract_evidence_roles(question)
    assert not {"needs_user_assignments", "needs_authority_checks"}.intersection(roles)
