import json


def _source():
    return [{
        "chunk_id": "source-1",
        "file_path": "src/security/Config.java",
        "class_name": "SecurityConfig",
        "symbol_name": "userDetailsService",
        "start_line": 10,
        "end_line": 20,
        "code_snippet": "UserDetailsService users = new InMemoryUserDetailsManager(firstUser, secondUser);",
    }]


def _existence():
    return [
        {"concept_searched": "authority checks", "existence_result": "found"},
        {"concept_searched": "authority hierarchy", "existence_result": "not_found"},
    ]


def _payload(refs):
    return json.dumps({"answer": "Grounded answer", "confidence": "high", "evidence_refs": refs, "helper_chain": [], "limitations": [], "needs_review": False})


def test_structured_parser_accepts_valid_source_and_existence_namespaces():
    from app.services.audit_service import parse_chat_answer

    parsed, status, invalid = parse_chat_answer(_payload(["E1", "X2"]), _source(), _existence())
    assert status == "valid_json"
    assert invalid == []
    assert parsed.evidence_refs == ["source-1", "X2"]


def test_structured_parser_keeps_real_invalid_e_and_x_warnings():
    from app.services.audit_service import parse_chat_answer

    parsed, status, invalid = parse_chat_answer(_payload(["E99", "X99", "E1"]), _source(), _existence())
    assert status == "valid_with_dropped_invalid_evidence_refs"
    assert invalid == ["E99", "X99"]
    assert parsed.evidence_refs == ["source-1"]


def test_reference_validator_separates_valid_e_and_x_counts_and_warns_unknown_ids():
    from app.services.methodology import validate_model_references

    valid = validate_model_references("Supported by [E1] and [X2].", _source(), _existence())
    invalid = validate_model_references("Unsupported [E9] and [X99].", _source(), _existence())
    assert valid["model_reference_warnings"] == []
    assert valid["source_evidence_cited_count"] == 1
    assert valid["existence_evidence_referenced_count"] == 1
    assert valid["referenced_existence_evidence_ids"] == ["X2"]
    assert {item["code"] for item in invalid["model_reference_warnings"]} == {
        "invalid_model_source_evidence_reference", "invalid_model_existence_evidence_reference",
    }


def test_type_identifier_in_source_or_metadata_is_supported_but_absent_identifier_warns():
    from app.services.methodology import validate_model_references

    source_supported = validate_model_references("UserDetailsService uses InMemoryUserDetailsManager.", _source())
    metadata_supported = validate_model_references("SecurityConfig creates users.", _source())
    unsupported = validate_model_references("HallucinatedAccessManager grants access.", _source())
    assert source_supported["model_reference_warnings"] == []
    assert metadata_supported["model_reference_warnings"] == []
    assert any(item["code"] == "invalid_model_symbol_reference" and item["claim"] == "HallucinatedAccessManager" for item in unsupported["model_reference_warnings"])


def test_stored_q7_style_answers_keep_x2_and_source_supported_types_without_false_warnings():
    from app.services.audit_service import parse_chat_answer
    from app.services.methodology import validate_model_references

    for answer in (
        "The indexed-source search found no hierarchy [X2]. InMemoryUserDetailsManager stores users.",
        "No authority inheritance was found (X2); UserDetailsService is configured in the supplied code.",
    ):
        result = validate_model_references(answer, _source(), _existence())
        assert result["model_reference_warnings"] == []
        assert result["existence_evidence_referenced_count"] == 1
        parsed, status, invalid = parse_chat_answer(_payload(["X2"]), _source(), _existence())
        assert status == "valid_json" and invalid == [] and parsed.evidence_refs == ["X2"]


def test_raw_answer_is_never_rewritten_by_reference_validation():
    from app.services.methodology import validate_model_references

    answer = "HallucinatedAccessManager is unsupported [X99]."
    validate_model_references(answer, _source(), _existence())
    assert answer == "HallucinatedAccessManager is unsupported [X99]."
