import { expect, test, type Page } from "@playwright/test";

const fullGemini = "# Complete Gemini answer\n\nOpening detail.\n\nFinal stored detail.";
const fullGroq = "# Complete Groq answer\n\nIndependent model detail.";
const execution = {
  execution_id: "execution-copy-raw",
  started_at: "2026-08-18T10:00:00Z",
  completed_at: "2026-08-18T10:00:01Z",
  operation: "compare",
  status: "completed",
  query: "generic copy question",
  provider: { provider: "gemini", model: "cloud", request_duration_ms: 1000, source_chunk_count: 2 },
  processing: { raw_response: "complete raw diagnostic content", nested: { retained: true } },
};

async function setup(page: Page) {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/models/health", route => route.fulfill({ json: { ollama: { available: true, status: "Ready", available_models: ["local"], default_model: "local" }, gemini: { available: true, status: "Ready", default_model: "cloud" }, groq: { available: true, status: "Ready", available_models: ["groq-model"], default_model: "groq-model" }, openai: { available: false, status: "Not configured" }, embedding: {} } }));
  await page.route("**/api/projects/copy/status", route => route.fulfill({ json: { status: "indexed", project: { id: "copy", name: "Copy", source_type: "local", local_path: "repo", status: "indexed", created_at: "now", updated_at: "now" } } }));
  await page.route("**/api/projects/copy/files/tree", route => route.fulfill({ json: [] }));
  await page.route("**/api/projects/copy/wiki", route => route.fulfill({ json: [] }));
  await page.route("**/api/projects/copy/usage", route => route.fulfill({ json: { overview: { requests: 0 }, recent_executions: [] } }));
  await page.route("**/api/projects/copy/formal-runs", route => route.fulfill({ json: [{
    run_id: "run-copy", operation: "compare", question: "copy behavior", timestamp: new Date().toISOString(),
    provider_model_json: JSON.stringify([{ provider: "gemini", model: "cloud" }, { provider: "groq", model: "groq-model" }]),
    answer_json: JSON.stringify([
      { evaluation_id: "g", selection_id: "gemini::cloud", provider: "gemini", model: "cloud", answer: "PREVIEW ONLY", answer_preview: "PREVIEW ONLY", full_answer: fullGemini, validation_status: "valid_json", supplied_source_count: 2, cited_source_count: 1, execution },
      { evaluation_id: "r", selection_id: "groq::groq-model", provider: "groq", model: "groq-model", answer: fullGroq, full_answer: fullGroq, validation_status: "valid_json", supplied_source_count: 2, cited_source_count: 1 },
    ]),
    primary_evidence_json: "[]", wiki_context_json: "[]", comparison_metadata_json: JSON.stringify({ rq2_comparison_eligible: true }), execution_status: "completed", run_purpose: "development",
  }] }));
  await page.goto("/projects/copy");
  await page.getByRole("button", { name: "History", exact: true }).click();
  await page.getByText(/copy behavior/).click();
}

test("full-answer copy is canonical, visible, temporary, keyboard accessible, and card-local", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await setup(page);
  const buttons = page.getByRole("button", { name: "Copy full answer" });
  await buttons.first().focus();
  await buttons.first().press("Enter");
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  await expect(buttons).toHaveCount(1);
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toBe(fullGemini);
  await expect(page.getByText("PREVIEW ONLY")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Copy full answer" })).toBeVisible();
  await page.waitForTimeout(1900);
  await expect(page.getByRole("button", { name: "Copy full answer" })).toHaveCount(2);
});

test("clipboard failure is visible and non-destructive", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: () => Promise.reject(new Error("denied")) } }));
  await page.getByRole("button", { name: "Copy full answer" }).first().click();
  await expect(page.getByRole("button", { name: "Copy failed" })).toBeVisible();
  await expect(page.getByText("Final stored detail.")).toBeVisible();
});

test("raw diagnostics copy uses the complete canonical serialization", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await setup(page);
  await page.getByText("Run details").first().click();
  await page.getByText("Advanced technical diagnostics").first().click();
  const rawCopy = page.getByRole("button", { name: "Copy raw content" }).first();
  await expect(rawCopy).toBeVisible();
  await rawCopy.click();
  await expect(page.getByRole("button", { name: "Copied raw content" })).toBeVisible();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toBe(JSON.stringify(execution, null, 2));
});
