import pytest

from app.services.project_service import _extract_evidence_roles, _has_enumeration_intent


@pytest.mark.parametrize(
    "question",
    [
        "Which account mutations are denied after suspension?",
        "Which document writes are blocked once a record is archived?",
        "Which operations require administrator approval?",
        "Which fields become read-only after publication?",
        "Which API writes are prevented after finalization, and what evidence proves each restriction?",
    ],
)
def test_plural_which_questions_request_enumeration(question):
    assert _has_enumeration_intent(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "Which method handles login?",
        "Which file contains the parser?",
        "Which role owns this record?",
        "Which function creates the object?",
        "Which class parses the request?",
    ],
)
def test_singular_which_locator_questions_do_not_enumerate(question):
    assert _has_enumeration_intent(question) is False


@pytest.mark.parametrize(
    "question,enumerates",
    [
        ("Which financial writes are blocked after an accounting period is finalized?", True),
        ("Which document updates are denied once a record is archived?", True),
        ("Can an account be modified after it is suspended?", False),
        ("Which fields become read-only after publication?", True),
        ("Which operations are prevented after a workflow is completed?", True),
        ("Can this object be deleted after the billing cycle is locked?", False),
    ],
)
def test_state_restriction_questions_request_policy_evidence(question, enumerates):
    assert _has_enumeration_intent(question) is enumerates
    assert "needs_authority_checks" in _extract_evidence_roles(question)


@pytest.mark.parametrize(
    "question",
    [
        "Can a parser update its cache after parsing completes?",
        "Which files are generated after the build finishes?",
        "Can a user edit a draft before publication?",
        "Which theme changes happen after startup?",
        "Can a logger rotate files after midnight?",
        "Which values change after calculation finishes?",
    ],
)
def test_state_words_without_restriction_do_not_request_policy_evidence(question):
    assert "needs_authority_checks" not in _extract_evidence_roles(question)


def test_exact_android_q5_requests_both_planner_signals():
    question = "Which financial writes are blocked after a month is closed in Dunio, and what source evidence proves each restriction?"
    assert _has_enumeration_intent(question) is True
    assert "needs_authority_checks" in _extract_evidence_roles(question)
