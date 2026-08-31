import { expect, test } from "@playwright/test";

test("Groq is selectable, renders a full Compare answer, and refreshes cost telemetry", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  let usageCalls = 0;
  const full = "# Groq result\n\nOpening authorization finding (E1).\n\n## Complete path\n\nFinal Groq detail is preserved.";
  await page.route("**/api/models/health", r => r.fulfill({ json: {
    ollama: { available: true, status: "Ready", available_models: ["qwen3.5:9b"], default_model: "qwen3.5:9b" },
    gemini: { available: true, status: "Ready", default_model: "gemini-2.5-flash" },
    groq: { available: true, reachable: true, status: "Ready", reason: "Groq provider reachable", available_models: ["openai/gpt-oss-20b"], default_model: "openai/gpt-oss-20b", default_model_exists: true },
    openai: { available: false, status: "Not configured", reason: "OPENAI_API_KEY not set", default_model: "gpt-4o-mini" }, embedding: {}
  }}));
  await page.route("**/api/projects/groq/status", r => r.fulfill({ json: { status: "indexed", project: { id: "groq", name: "Groq", source_type: "local", local_path: "repo", status: "indexed", created_at: "now", updated_at: "now" } } }));
  await page.route("**/api/projects/groq/files/tree", r => r.fulfill({ json: [] }));
  await page.route("**/api/projects/groq/wiki", r => r.fulfill({ json: [] }));
  await page.route("**/api/projects/groq/formal-runs", r => r.fulfill({ json: [] }));
  await page.route("**/api/projects/groq/usage", r => {
    usageCalls++;
    return r.fulfill({ json: { overview: { requests: usageCalls > 1 ? 1 : 0, actual_provider_api_cost: usageCalls > 1 ? .000105 : null }, scenario_pricing: {}, by_model: usageCalls > 1 ? [{ model: "openai/gpt-oss-20b", provider: "groq", calls: 1, input_tokens: 1000, output_tokens: 100, reasoning_tokens: 40, total_tokens: 1100, latency_ms: 70, actual_provider_api_cost: .000105 }] : [], by_operation: [], recent_executions: [] } });
  });
  await page.route("**/api/projects/groq/compare-models", r => r.fulfill({ json: {
    results: [{ evaluation_id: "eval", selection_id: "groq::openai/gpt-oss-20b", provider: "groq", model: "openai/gpt-oss-20b", answer: full, full_answer: full, validation_status: "valid_json", display_status: "Completed", supplied_source_count: 1, cited_source_count: 1 }],
    evidence: [], wiki_context: [], run_summary: { execution_id: "run", comparison_valid: false, rq2_comparison_eligible: false, primary_evidence_match: true, wiki_context_match: true, shared_evidence_hash: "hash", selected_models: [{ provider: "groq", model: "openai/gpt-oss-20b" }], total_duration: 70 }
  }}));

  await page.goto("/projects/groq");
  await expect(page.getByRole("option", { name: /openai\/gpt-oss-20b.*Groq/i })).toBeEnabled();
  await page.getByLabel("Default model").selectOption("groq");
  await expect(page.getByLabel("Groq model")).toHaveValue("openai/gpt-oss-20b");
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  const groq = page.getByRole("checkbox", { name: /Groq \/ openai\/gpt-oss-20b/i });
  await expect(groq).toBeChecked();
  await groq.uncheck(); await expect(groq).not.toBeChecked(); await groq.check();
  const gemini = page.getByRole("checkbox", { name: /Gemini/ }); if (await gemini.isChecked()) await gemini.uncheck();
  await page.getByLabel("Question").fill("Trace authorization");
  await page.getByRole("button", { name: "Compare models" }).click();
  await expect(page.getByText("Final Groq detail is preserved.")).toBeVisible();
  await page.getByRole("button", { name: "Copy full answer" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Final Groq detail is preserved.");
  await page.getByLabel("Model settings").click(); await page.getByRole("button", { name: /Usage & Cost/ }).click(); await page.getByRole("button", { name: "Models", exact: true }).click();
  const usageDialog = page.getByRole("dialog", { name: "Usage & Cost" });
  await expect(usageDialog.getByText("openai/gpt-oss-20b", { exact: true })).toBeVisible();
  await expect(usageDialog.getByText("groq", { exact: true })).toBeVisible();
  await expect(usageDialog.getByText("40", { exact: true })).toBeVisible();
  await expect(usageDialog.getByText("$0.000105", { exact: true })).toBeVisible();
  expect(usageCalls).toBeGreaterThan(1);
});

test("Groq missing-key reason is visible and disabled", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/models/health", r => r.fulfill({ json: { ollama: { available: true, status: "Ready", available_models: ["local"], default_model: "local" }, gemini: { available: false, status: "Not configured" }, groq: { available: false, status: "Not configured", reason: "GROQ_API_KEY not configured", available_models: [], default_model: "openai/gpt-oss-20b" }, openai: { available: false, status: "Not configured" }, embedding: {} } }));
  await page.route("**/api/projects/missing/status", r => r.fulfill({ json: { status: "indexed", project: { id: "missing", name: "Missing", source_type: "local", local_path: "repo", status: "indexed", created_at: "now", updated_at: "now" } } }));
  for (const suffix of ["files/tree", "wiki", "formal-runs"]) await page.route(`**/api/projects/missing/${suffix}`, r => r.fulfill({ json: [] }));
  await page.route("**/api/projects/missing/usage", r => r.fulfill({ json: { overview: { requests: 0 }, by_model: [], by_operation: [], recent_executions: [] } }));
  await page.goto("/projects/missing"); await page.getByLabel("Model settings").click(); await page.getByRole("button", { name: /Models & Providers/ }).click();
  await expect(page.getByText("GROQ_API_KEY not configured")).toBeVisible();
  expect(await page.getByRole("option", { name: /Groq.*unavailable/i }).evaluate((option: HTMLOptionElement) => option.disabled)).toBe(true);
});

test("zero-model mode keeps deterministic workspace available and blocks LLM execution", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  const unavailable = (model: string, reason: string) => ({ available: false, status: "Not configured", reason, default_model: model, available_models: [] });
  await page.route("**/api/models/health", r => r.fulfill({ json: {
    gemini: unavailable("gemini-2.5-flash", "GEMINI_API_KEY not set"),
    openrouter: unavailable("openai/gpt-5.1", "OPENROUTER_API_KEY not set"),
    openai: unavailable("gpt-4o-mini", "OPENAI_API_KEY not set"),
    groq: unavailable("openai/gpt-oss-20b", "GROQ_API_KEY not configured"),
    ollama: { ...unavailable("qwen3.5:9b", "Ollama local server not responding"), reachable: false, default_model_exists: false, base_url: "local" },
    embedding: { provider: "hash", model: "test", semantic: false, fallback_used: true, label: "Development hash fallback" },
  } }));
  await page.route("**/api/projects/zero/status", r => r.fulfill({ json: { status: "indexed", project: { id: "zero", name: "Zero", source_type: "local", local_path: "repo", status: "indexed", created_at: "now", updated_at: "now" } } }));
  for (const suffix of ["files/tree", "wiki", "formal-runs"]) await page.route(`**/api/projects/zero/${suffix}`, r => r.fulfill({ json: [] }));
  await page.route("**/api/projects/zero/usage", r => r.fulfill({ json: { overview: { requests: 0 }, by_model: [], by_operation: [], recent_executions: [] } }));

  await page.goto("/projects/zero");
  await expect(page.getByRole("button", { name: "Discover", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("No language model is currently available.")).toBeVisible();
  await page.getByLabel("Question").fill("Where is access control enforced?");
  await expect(page.getByRole("button", { name: "Run analysis" })).toBeDisabled();
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(page.getByRole("button", { name: "Compare models" })).toBeDisabled();
});
