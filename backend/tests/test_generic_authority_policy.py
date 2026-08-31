from pathlib import Path

import pytest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "generic_policy_sample"


def _item(name: str) -> dict:
    path = FIXTURE_ROOT / name
    return {
        "file_path": f"config/{name}",
        "symbol_name": None,
        "start_line": 1,
        "security_tags": "potential_access_check",
        "code_snippet": path.read_text(encoding="utf-8"),
    }


@pytest.mark.parametrize("name", ["conditional_allow.txt", "owner_member.txt", "permit_deny.txt"])
def test_concrete_conditional_policies_satisfy_authority_role(name):
    from app.services.project_service import _classify_evidence_roles

    assert "needs_authority_checks" in _classify_evidence_roles(_item(name))


@pytest.mark.parametrize(
    "name",
    ["ui_strings.txt", "permissions_documentation.txt", "translated_strings.txt", "owner_member_notes.txt"],
)
def test_crud_prose_and_owner_member_mentions_do_not_satisfy_authority_role(name):
    from app.services.project_service import _classify_evidence_roles

    assert "needs_authority_checks" not in _classify_evidence_roles(_item(name))


def test_authority_coverage_promotes_a_lower_ranked_generic_policy():
    from app.services.project_service import _apply_evidence_role_coverage

    distractions = [
        {
            "chunk_id": f"ui-{index}",
            "file_path": "res/strings.txt",
            "symbol_name": None,
            "start_line": index,
            "code_snippet": "Owners can create, read, update, and delete records.",
            "final_score": 1.0 - index / 100,
        }
        for index in range(10)
    ]
    policy = {
        **_item("conditional_allow.txt"),
        "chunk_id": "policy",
        "final_score": 0.1,
    }

    result = _apply_evidence_role_coverage(
        distractions,
        [*distractions, policy],
        {"needs_authority_checks"},
        top_k=10,
    )

    assert len(result) == 10
    assert "policy" in {item["chunk_id"] for item in result}
