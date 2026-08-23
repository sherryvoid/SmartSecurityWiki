import { expect, test, type Page } from "@playwright/test";

const evidence = [
  { chunk_id: "get", file_path: "src/ProductController.java", symbol_name: "getProducts", start_line: 8, end_line: 12, language: "java", code_snippet: "@GetMapping\nvoid getProducts() {}", critical_lines: [9], final_score: 1.2, retrieval_rank: 8, prompt_position: 1, evidence_priority_class: "target_primary", http_method: "GET", class_route_state: "absent", method_route_state: "explicit_empty", effective_route: "/", route_resolution_status: "resolved" },
  { chunk_id: "users", file_path: "src/WebSecurityConfig.java", symbol_name: "users", start_line: 20, end_line: 24, language: "java", code_snippet: "User.withUsername(\"demo\").authorities(\"READ\")", critical_lines: [21], final_score: 1.1, retrieval_rank: 7, prompt_position: 2, evidence_priority_class: "required_supporting_role" },
];

async function mockWorkspace(page: Page) {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/models/health", route => route.fulfill({ json: {
    ollama: { reachable: true, available: true, reason: "Ready", available_models: ["qwen"], default_model: "qwen", default_model_exists: true, status: "Ready", base_url: "local" },
    gemini: { available: true, reason: "Ready", status: "Ready", api_key_configured: true, default_model_configured: true, default_model: "gemini" },
    openai: { available: false, reason: "API key not configured", status: "Unavailable", api_key_configured: false, default_model_configured: true, default_model: "gpt" },
    deepseek: { available: false, reason: "API key not configured", status: "Unavailable", api_key_configured: false, default_model_configured: true, default_model: "deepseek" },
    embedding: { provider: "hash", model: "test", semantic: false, fallback_used: true, label: "test" }
  }}));
  await page.route("**/api/projects/phase44/status", route => route.fulfill({ json: { status: "indexed", status_message: "Ready", project: { id: "phase44", name: "Audit Project", source_type: "github", local_path: "repo", status: "indexed", status_message: "Ready", created_at: "now", updated_at: "now" } } }));
  await page.route("**/api/projects/phase44/files/tree", route => route.fulfill({ json: [{ name: "ProductController.java", path: "src/ProductController.java", type: "file" }] }));
  await page.route("**/api/projects/phase44/files/content?**", async route => {
    const path = new URL(route.request().url()).searchParams.get("path") || "";
    const content = Array.from({ length: 40 }, (_, i) => `// line ${i + 1}`).join("\n");
    await route.fulfill({ json: { path, content, language: "java" } });
  });
  await page.route("**/api/projects/phase44/wiki", route => route.fulfill({ json: [{ id: "wiki", title: "Wiki", module_id: "src/ProductController.java", updated_at: "now", content_markdown: "| Name | File | Lines | Description |\n| --- | --- | --- | --- |\n| getProducts | src/ProductController.java | 8-12 | endpoint |" }] }));
  await page.route("**/api/projects/phase44/chat", route => route.fulfill({ json: { message_id: "answer", answer: "Grounded answer", evidence, wiki_context: [], context_used: "raw code evidence only", validation_status: "valid_json", display_status: "Completed", provider: "gemini", model: "gemini", execution: { execution_id: "ask", started_at: "now", completed_at: "now", operation: "ask", status: "completed", query: "q", provider: { provider: "gemini", model: "gemini", request_duration_ms: 4 }, retrieval: {}, processing: {} } } }));
  await page.route("**/api/projects/phase44/compare-models", route => route.fulfill({ json: { question: "q", evidence, wiki_context: [], comparison_valid: true, comparison_invalid_reason: null, shared_evidence_package_id: "abc123", shared_evidence_hash: "hash", excluded_providers: [], run_summary: { execution_id: "compare", question: "q", shared_evidence_package_id: "abc123", shared_evidence_hash: "hash", comparison_valid: true, comparison_invalid_reason: null, started_at: "now", completed_at: "now", total_duration: 8, selected_models: [{ provider: "gemini", model: "gemini" }, { provider: "ollama", model: "qwen" }] }, results: [{ evaluation_id: "eval", provider: "gemini", model: "gemini", answer: "Answer", latency_ms: 4, validation_status: "completed", display_status: "Completed", shared_evidence_package_id: "abc123", shared_evidence_hash: "hash", serialized_chunk_ids: ["get", "users"], evidence_package_match: true, evaluation_status: "not_scored", execution: { execution_id: "provider", started_at: "now", completed_at: "now", operation: "compare", status: "completed", query: "q", provider: { provider: "gemini", model: "gemini", request_duration_ms: 4 }, retrieval: {}, processing: {} } }] } }));
  await page.goto("/projects/phase44");
  await expect(page.locator(".workspace")).toBeVisible();
}

async function runAsk(page: Page) {
  await page.getByRole("button", { name: "ask", exact: true }).click();
  await page.getByPlaceholder(/where is access control enforced/i).fill("q");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("Grounded answer")).toBeVisible();
}

test("evidence navigation reveals and decorates Monaco while preserving result", async ({ page }) => {
  await mockWorkspace(page); await runAsk(page);
  const first = page.locator(".evidence-card-collapsible").first();
  await first.locator(":scope > summary").click();
  await expect(page.locator(".source-breadcrumb")).toContainText("ProductController.java");
  await expect(page.locator(".evidence-line-highlight").first()).toBeVisible();
  await expect(page.locator(".critical-line-highlight").first()).toBeVisible();
  await page.getByRole("button", { name: "Back to Analysis" }).click();
  await expect(page.getByText("Grounded answer")).toBeVisible();
  await page.locator(".evidence-card-collapsible").nth(1).locator(":scope > summary").click();
  await expect(page.locator(".source-breadcrumb")).toContainText("WebSecurityConfig.java");
  await expect(page.locator(".evidence-card-collapsible.active")).toContainText("WebSecurityConfig.java");
});

test("wiki and restored Run History evidence use source navigation", async ({ page }) => {
  await mockWorkspace(page);
  await page.getByRole("button", { name: /security wiki/i }).click();
  await page.getByText(/Generated Wikis/).click();
  await page.getByText("View wiki").click();
  await page.getByRole("button", { name: "Open Source" }).click();
  await expect(page.locator(".source-breadcrumb")).toContainText("ProductController.java");
  await page.getByRole("button", { name: "Back to Analysis" }).click();
  await runAsk(page);
  await page.getByRole("button", { name: /run history/i }).click();
  await page.locator(".run-history-item").first().click();
  await page.getByRole("button", { name: "ask", exact: true }).click();
  await page.locator(".evidence-card-collapsible").first().locator(":scope > summary").click();
  await expect(page.locator(".evidence-line-highlight").first()).toBeVisible();
});

test("Compare disables unavailable providers and displays package equality without unknown execution", async ({ page }) => {
  await mockWorkspace(page);
  await page.getByRole("button", { name: "compare", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /OpenAI \/ gpt/ })).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: /DeepSeek \/ deepseek/ })).toBeDisabled();
  await page.getByPlaceholder(/where is access control enforced/i).fill("q");
  await page.getByRole("button", { name: /Compare selected models/ }).click();
  await expect(page.getByText("Controlled comparison valid")).toBeVisible();
  await expect(page.getByText(/Shared evidence:/).first()).toContainText("abc123");
  await expect(page.getByText("Match: Yes")).toBeVisible();
  await expect(page.getByText(/Unknown provider|Unknown model/)).toHaveCount(0);
  await expect(page.locator(".evaluation-drawer[open]")).toHaveCount(0);
});

test("legacy light theme and target viewports render", async ({ page }, testInfo) => {
  await mockWorkspace(page);
  const cases = [[1920,1080,"1920-light"],[1366,768,"1366-light"],[1024,768,"1024"],[390,844,"390"]] as const;
  for (const [width,height,name] of cases) {
    await page.setViewportSize({ width, height });
    await expect(page.locator(".theme-switcher")).toHaveCount(0);
    await expect(page.locator("body")).toHaveCSS("background-color", "rgb(238, 242, 243)");
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
    await expect(page.locator(".workspace-tabs")).toBeVisible();
  }
});
