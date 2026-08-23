import { expect, test, type Page } from "@playwright/test";

const chain = "userDetails.getAuthorities().stream().map(GrantedAuthority::getAuthority).filter(authorityNameThatIsIntentionallyVeryLongForLayoutValidation).collect(Collectors.joining(\" \"))";
const path = "src/main/java/com/example/security/configuration/authorization/extremely/deep/package/ThisIsAnExtremelyLongSecurityConfigurationFilenameThatMustRemainInsideTheCard.java";
const token = "UNBROKEN_SECURITY_TOKEN_" + "A".repeat(220);
const answer = `# Overflow checks\n\nInline expression: \`${chain}\`\n\nLong path: ${path}\n\nLong token: ${token}\n\n- Nested list item with ${path}\n\n\`\`\`java\n${chain}\n\`\`\``;

async function setup(page: Page) {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/models/health", route => route.fulfill({ json: { ollama: { available: true, status: "Ready", available_models: ["local"], default_model: "local" }, gemini: { available: true, status: "Ready", default_model: "cloud" }, groq: { available: true, status: "Ready", available_models: ["groq"], default_model: "groq" }, openai: { available: false, status: "Not configured" }, embedding: {} } }));
  await page.route("**/api/projects/overflow/status", route => route.fulfill({ json: { status: "indexed", project: { id: "overflow", name: "Overflow", source_type: "local", local_path: "repo", status: "indexed", created_at: "now", updated_at: "now" } } }));
  await page.route("**/api/projects/overflow/files/tree", route => route.fulfill({ json: [] }));
  await page.route("**/api/projects/overflow/wiki", route => route.fulfill({ json: [] }));
  await page.route("**/api/projects/overflow/usage", route => route.fulfill({ json: { overview: { requests: 0 }, recent_executions: [] } }));
  await page.route("**/api/projects/overflow/formal-runs", route => route.fulfill({ json: [{
    run_id: "overflow-run", operation: "compare", question: "overflow fixture", timestamp: new Date().toISOString(),
    provider_model_json: JSON.stringify([{ provider: "gemini", model: "cloud" }, { provider: "groq", model: "groq" }]),
    answer_json: JSON.stringify([
      { evaluation_id: "g", selection_id: "gemini::cloud", provider: "gemini", model: "cloud", answer, full_answer: answer, validation_status: "valid_json" },
      { evaluation_id: "r", selection_id: "groq::groq", provider: "groq", model: "groq", answer, full_answer: answer, validation_status: "valid_json" },
    ]), primary_evidence_json: "[]", wiki_context_json: "[]", comparison_metadata_json: JSON.stringify({ rq2_comparison_eligible: true }), execution_status: "completed", run_purpose: "development",
  }] }));
  await page.goto("/projects/overflow");
  await page.getByRole("button", { name: "History", exact: true }).click();
  await page.getByText(/overflow fixture/).click();
}

async function expectContained(page: Page) {
  const geometry = await page.locator(".comparison-grid").evaluate(grid => {
    const parent = grid.getBoundingClientRect();
    const cards = [...grid.querySelectorAll<HTMLElement>(".model-answer")];
    return {
      columns: getComputedStyle(grid).gridTemplateColumns,
      parent: { left: parent.left, right: parent.right },
      cards: cards.map(card => { const rect = card.getBoundingClientRect(); return { left: rect.left, right: rect.right, clientWidth: card.clientWidth, scrollWidth: card.scrollWidth }; }),
      preScrollable: cards.every(card => { const pre = card.querySelector<HTMLElement>("pre"); return Boolean(pre && pre.scrollWidth >= pre.clientWidth && getComputedStyle(pre).overflowX === "auto"); }),
    };
  });
  expect(geometry.cards).toHaveLength(2);
  for (const card of geometry.cards) {
    expect(card.left).toBeGreaterThanOrEqual(geometry.parent.left - 1);
    expect(card.right).toBeLessThanOrEqual(geometry.parent.right + 1);
    expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth + 1);
  }
  expect(geometry.preScrollable).toBe(true);
  return geometry;
}

test("long answer content stays inside both Compare cards", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await setup(page);
  const desktop = await expectContained(page);
  expect(desktop.columns.trim().split(/\s+/)).toHaveLength(2);
  await page.setViewportSize({ width: 900, height: 1000 });
  await expectContained(page);
  await expect(page.getByText(token).first()).toBeVisible();
});
