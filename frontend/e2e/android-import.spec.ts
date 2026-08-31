import { expect, test } from "@playwright/test";

test("Android repository import uses the pasted URL without a case-study selector", async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  let requestedCaseStudies = false;
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/projects/android-case-studies", route => {
    requestedCaseStudies = true;
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/projects", async route => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      await route.fulfill({ json: { id: "android-project", name: "Android Study", source_type: "android", local_path: "repo", subfolder_path: "services/accounts", status: "created", created_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z" } });
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Android" }).click();
  await expect(page.getByText("Android Case Study")).toHaveCount(0);
  await page.getByLabel("Project name").fill("Android Study");
  await page.getByLabel("Android Project Link").fill("https://example.invalid/android.git");
  await page.getByLabel("Optional Subfolder Path").fill("services/accounts");
  await page.getByLabel(/Security goal/).fill("Inspect account permissions");
  await page.getByRole("button", { name: "Create Project" }).click();

  await expect.poll(() => submitted).toBeTruthy();
  expect(requestedCaseStudies).toBe(false);
  expect(submitted).toMatchObject({ name: "Android Study", source_type: "android", android_source_url: "https://example.invalid/android.git", subfolder_path: "services/accounts", security_goal: "Inspect account permissions" });
  expect(submitted).not.toHaveProperty("android_case_study");
});
