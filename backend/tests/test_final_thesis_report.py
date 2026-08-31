from app.services.report_service import normalize_text_items, render_html, render_markdown, render_pdf


def _report():
    evaluation = {"correctness": 3, "completeness": 2, "source_reference_accuracy": 2, "evidence_discipline": 3, "explanation_quality": 3, "usefulness": 3, "hallucination": False, "verdict": "Verified", "notes": "Checked.", "saved_at": "2026-08-30T12:00:00+00:00"}
    usage = {"execution_id": "model-exec", "input_tokens": 20, "output_tokens": 10, "total_tokens": 35, "cached_tokens": 2, "reasoning_tokens": 3, "duration_ms": 900, "load_duration_ms": None, "prompt_evaluation_duration_ms": None, "generation_duration_ms": None}
    evidence = [{"file_path": "src/Auth.java", "symbol_name": "check", "start_line": 4, "end_line": 9, "code_snippet": "if (!allowed) throw denied;"}]
    base = {"started_at": "2026-08-30T11:59:59+00:00", "completed_at": "2026-08-30T12:00:00+00:00", "status": "completed", "run_purpose": "formal_evaluation", "selected_file": None, "evidence_package_id": "pkg", "evidence_hash": "hash", "wiki_hash": "wiki", "source_count": 1, "wiki_count": 0, "evidence_package_match": True, "effective_context_valid": True, "requested_roles": [], "satisfied_roles": [], "unsatisfied_roles": [], "evidence": evidence}
    ask = {**base, "operation": "ask", "execution_id": "ask-run", "results": [{"provider": "gemini", "model": "gemini-2.5-flash", "status": "completed", "display_status": "Completed", "answer": "**Endpoint**\n\n- **Method:** GET\n\n```java\ncheck();\n```", "warnings": [], "error": None, "usage": usage, "human_evaluation": evaluation}]}
    compare_results = [
        {"provider": "openrouter", "model": "openai/gpt-5.1", "status": "completed_with_warnings", "display_status": "Completed with warnings", "answer": "## Access-control summary\n\nFull answer " + "detail " * 100, "warnings": [{"message": "Parser fallback used."}], "error": None, "usage": usage, "human_evaluation": evaluation},
        {"provider": "ollama", "model": "qwen3.5:9b", "status": "completed", "display_status": "Completed", "answer": "## Limitations\n\n- None; all endpoints are defined.", "warnings": [], "error": None, "usage": usage, "human_evaluation": None},
        {"provider": "groq", "model": "openai/gpt-oss-20b", "status": "provider_unavailable", "display_status": "Provider unavailable", "answer": "Not produced", "warnings": [], "error": {"user_message": "Rate limit"}, "usage": usage, "human_evaluation": None},
    ]
    compare = {**base, "operation": "compare", "execution_id": "compare-run", "results": compare_results}
    return {"project": {"name": "Synthetic", "repo_url": "https://example.test/repo"}, "generated_at": "2026-08-30T12:01:00+00:00", "timezone": "UTC", "auditor_name": "admin", "questions": [{"question_text": "Question one?", "first_execution_at": ask["started_at"], "executions": [ask, compare]}, {"question_text": "Question two?", "first_execution_at": ask["started_at"], "executions": [{**ask, "execution_id": "ask-two"}]}], "counts": {"questions": 2, "executions": 3, "ask_runs": 2, "compare_runs": 1, "model_results": 5}}


def test_string_limitations_are_one_item_not_characters():
    value = "None; all endpoints, authorities, and user configurations are explicitly defined in the provided evidence."
    assert normalize_text_items(value) == [value]
    assert normalize_text_items(["First", "Second"]) == ["First", "Second"]
    assert normalize_text_items(None) == []


def test_all_formats_share_full_semantics_and_html_renders_markdown():
    report = _report()
    markdown = render_markdown(report)
    html = render_html(report)
    pdf = render_pdf(report)
    for expected in ("Question one?", "Initial Ask", "Model Comparison", "GPT-5.1", "Qwen 3.5 9B", "GPT-OSS 20B", "Checked.", "Comparison Summary", "Supporting Sources"):
        assert expected in markdown
        assert expected in html
    assert "Full answer " + "detail " * 100 in markdown
    assert "<strong>Endpoint</strong>" in html
    assert "<li><strong>Method:</strong> GET</li>" in html
    assert "**Endpoint**" not in html
    assert pdf.startswith(b"%PDF-")


def test_high_level_report_hides_internal_metadata_but_projection_retains_it():
    report = _report()
    markdown = render_markdown(report)
    for hidden in ("Execution ID:", "Model execution ID:", "Evidence package ID:", "Evidence hash:",
                   "Evidence package match:", "Effective context valid:", "Classification:",
                   "Status: valid_json", "Cached tokens:", "Reasoning tokens:", "Parser fallback used.", "if (!allowed)"):
        assert hidden not in markdown
    assert report["questions"][0]["executions"][0]["execution_id"] == "ask-run"
    assert report["questions"][0]["executions"][0]["evidence_hash"] == "hash"


def test_human_totals_and_unevaluated_results_are_presented_truthfully():
    markdown = render_markdown(_report())
    assert "**16/17**" in markdown
    assert "Qwen 3.5 9B | Not evaluated" in markdown
    assert "Saved at" not in markdown
    assert "Overall Model Results" in markdown


def test_html_has_question_navigation_and_no_raw_html_from_answers():
    report = _report()
    report["questions"][0]["executions"][0]["results"][0]["answer"] += "\n<script>alert(1)</script>"
    rendered = render_html(report)
    assert 'href="#question-1"' in rendered
    assert 'id="question-1"' in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_frontend_export_menu_only_offers_supported_formats():
    from pathlib import Path
    frontend = (Path(__file__).parents[2] / "frontend/src/pages/ProjectWorkspace.tsx").read_text(encoding="utf-8")
    menu = frontend.split('title="Export project report"', 1)[1].split("</Popover>", 1)[0]
    assert '>PDF<' in menu and '>HTML<' in menu and '>Markdown<' in menu
    assert '>JSON<' not in menu and '>CSV<' not in menu
