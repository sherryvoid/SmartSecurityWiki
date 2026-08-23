def _insert_fixture(connection, project_id="jwt-cross-file"):
    files = (
        ("mapper-file", "src/security/ClaimMapper.java"),
        ("setup-file", "src/security/SecuritySetup.java"),
        ("issuer-file", "src/auth/TokenIssuer.java"),
        ("endpoint-file", "src/api/AdminEndpoint.java"),
    )
    for file_id, path in files:
        connection.execute(
            """INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at)
               VALUES (?, ?, ?, 'java', 100, 30, 1, 'now')""",
            (file_id, project_id, path),
        )
    chunks = (
        ("mapper", "mapper-file", "customAuthoritiesConverter", """
            static final String AUTHORITIES_CLAIM_NAME = "permissions";
            JwtGrantedAuthoritiesConverter delegate = new JwtGrantedAuthoritiesConverter();
            delegate.setAuthoritiesClaimName(AUTHORITIES_CLAIM_NAME);
            delegate.setAuthorityPrefix("");
            JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
            converter.setJwtGrantedAuthoritiesConverter(delegate);
        """, "authentication,jwt"),
        ("setup", "setup-file", "securityChain", """
            http.oauth2ResourceServer(oauth -> oauth.jwt(jwt ->
                jwt.jwtAuthenticationConverter(customAuthoritiesConverter())));
            return http.build();
        """, "authentication,jwt"),
        ("issue", "issuer-file", "issueAccessToken", """
            Map<String, Object> claims = new HashMap<>();
            claims.put("permissions", user.permissions());
            return JWT.create().withPayload(claims).sign(algorithm);
        """, "authentication,jwt"),
        ("create-helper", "issuer-file", "createJwtForClaims", "return JWT.create().withPayload(claims).sign(algorithm);", "jwt,potential_helper"),
        ("endpoint", "endpoint-file", "deleteItem", "@DeleteMapping @PreAuthorize(\"hasAuthority('ADMIN')\") void deleteItem() {}", "delete,preauthorize,hasauthority"),
    )
    for chunk_id, file_id, symbol, code, tags in chunks:
        connection.execute(
            """INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
               VALUES (?, ?, ?, 'method', ?, 'Fixture', 1, 20, ?, ?, ?, 'now')""",
            (chunk_id, project_id, file_id, symbol, code, tags, f"code:{chunk_id}"),
        )


def test_generic_jwt_vertical_trace_preserves_producer_converter_wiring_and_issuer(isolated_env, monkeypatch):
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    with db() as connection:
        _insert_fixture(connection)
    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = (
        "Explain how the custom permissions claim becomes granted authorities, whether ROLE_ or SCOPE_ is added, "
        "how the converter is attached to JWT authentication, and where the claim is populated when the JWT is created."
    )
    with db() as connection:
        package = project_service.retrieve_evidence_package("jwt-cross-file", question, 4, connection)

    ids = {item["chunk_id"] for item in package["source_chunks"]}
    assert ids == {"mapper", "setup", "issue", "create-helper"}
    assert "endpoint" not in ids
    assert package["diagnostics"]["enumeration_intent"] is False
    assert set(package["diagnostics"]["requested_evidence_roles"]).issuperset({
        "needs_claim_definition", "needs_claim_population", "needs_token_creation",
        "needs_authority_conversion", "needs_authentication_wiring",
    })
    assert package["diagnostics"]["unsatisfied_evidence_roles"] == []


def test_content_based_role_validation_rejects_keyword_only_chunks():
    from app.services.project_service import _classify_evidence_roles

    generic = {"file_path": "JwtNotes.java", "symbol_name": "rolesClaim", "code_snippet": "// JWT roles claim converter token creation"}
    assert not {
        "needs_claim_population", "needs_token_creation", "needs_authority_conversion", "needs_authentication_wiring",
    }.intersection(_classify_evidence_roles(generic))


def test_identifier_and_symbol_signals_are_query_specific():
    from app.services.project_service import _query_match_signals

    exact = _query_match_signals("AUTHORITIES_CLAIM_NAME", "static final String AUTHORITIES_CLAIM_NAME", ["ClaimMapper"])
    prefix = _query_match_signals("createJwt", "String createJwtForClaims(Map claims)", ["createJwtForClaims"])
    unrelated = _query_match_signals("createJwt", "@PreAuthorize hasAuthority('ADMIN')", ["deleteItem"])
    assert exact["exact_identifier_relevance"] == 1.0
    assert prefix["symbol_relevance"] >= 0.9
    assert unrelated["symbol_relevance"] == 0.0


def test_discover_ranking_and_reasons_change_with_query(isolated_env):
    from app.db.database import db, init_db
    from app.services.project_service import discover_security_modules

    init_db()
    with db() as connection:
        _insert_fixture(connection)
    constant_results = discover_security_modules("jwt-cross-file", "AUTHORITIES_CLAIM_NAME")
    creation_results = discover_security_modules("jwt-cross-file", "createJwt")
    assert constant_results[0]["module_path"].endswith("ClaimMapper.java")
    assert "exact identifier match" in constant_results[0]["reason"]
    assert creation_results[0]["module_path"].endswith("TokenIssuer.java")
    assert "symbol match: createJwtForClaims" in creation_results[0]["reason"]


def test_generic_security_chain_plans_and_packs_every_conceptual_stage(isolated_env, monkeypatch):
    """The vocabulary and symbols deliberately differ from the product fixture."""
    from app.db.database import db, init_db
    from app.services import project_service

    init_db()
    project_id = "generic-security-chain"
    files = [(f"f{i}", f"lib/layer_{i}.py") for i in range(1, 9)]
    chunks = (
        ("verify", "f1", "open_session", "account = directory.lookup(handle)\nif not argon2.verify(account.password_hash, password): raise AccessDenied()", "authentication,password"),
        ("mint", "f2", "mint_session", "return token_factory.issue(subject=account.id, permissions=account.permissions)", "authentication,token"),
        ("decode", "f3", "inspect_envelope", "payload = verifier.verify(incoming_token, public_key)\nreturn payload", "authentication,token"),
        ("convert", "f4", "adapt_identity", "identity_adapter(claims_to_permissions(payload))", "authentication,authorization"),
        ("protect", "f5", "change_record", "@route('/records', methods=['POST'])\n@permission_required('record:write')\ndef change_record(): pass", "authorization,post"),
        ("noise1", "f6", "get_name", "def get_name(self): return self.name", ""),
        ("noise2", "f7", "set_name", "def set_name(self, value): self.name = value", ""),
        ("noise3", "f8", "record", "class Record: pass", ""),
    )
    with db() as connection:
        for file_id, path in files:
            connection.execute("INSERT INTO files (id, project_id, file_path, language, size_bytes, line_count, is_indexed, created_at) VALUES (?, ?, ?, 'python', 100, 20, 1, 'now')", (file_id, project_id, path))
        for chunk_id, file_id, symbol, code, tags in chunks:
            connection.execute("""INSERT INTO code_chunks (id, project_id, file_id, chunk_type, symbol_name, class_name, start_line, end_line, code, security_tags, embedding_id, created_at)
                                  VALUES (?, ?, ?, 'function', ?, 'Layer', 1, 10, ?, ?, ?, 'now')""", (chunk_id, project_id, file_id, symbol, code, tags, f"code:{chunk_id}"))

    monkeypatch.setattr(project_service, "vector_query", lambda *args, **kwargs: [])
    question = (
        "Walk through sign-in: how is a submitted password checked, how is a session token minted, "
        "how does a received token get verified and turned into a security identity, and which protected "
        "operation performs the permission decision?"
    )
    with db() as connection:
        package = project_service.retrieve_evidence_package(project_id, question, 5, connection)

    required = {
        "needs_credential_authentication", "needs_token_creation", "needs_token_validation",
        "needs_authentication_wiring", "needs_authority_conversion",
        "needs_endpoint_declarations", "needs_authority_checks",
    }
    assert required.issubset(package["diagnostics"]["requested_evidence_roles"])
    assert required.issubset(package["diagnostics"]["satisfied_evidence_roles"])
    assert package["diagnostics"]["unsatisfied_evidence_roles"] == []
    assert {item["chunk_id"] for item in package["source_chunks"]} == {"verify", "mint", "decode", "convert", "protect"}


def test_coverage_replacement_does_not_evict_a_unique_role():
    from app.services.project_service import _apply_evidence_role_coverage

    def chunk(chunk_id, code):
        return {"chunk_id": chunk_id, "code_snippet": code, "file_path": f"lib/{chunk_id}.py"}

    verify = chunk("verify", "argon2.verify(account.password_hash, password)")
    mint = chunk("mint", "token_factory.issue(subject=account.id)")
    noise = chunk("noise", "def get_name(self): return self.name")
    decode = chunk("decode", "payload = verifier.verify(incoming_token, public_key)")
    requested = {"needs_credential_authentication", "needs_token_creation", "needs_token_validation"}
    result = _apply_evidence_role_coverage([verify, mint, noise], [verify, mint, noise, decode], requested, 3)
    assert {item["chunk_id"] for item in result} == {"verify", "mint", "decode"}
