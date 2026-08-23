import { expect, test, type Page } from '@playwright/test';

const username = process.env.TEST_USERNAME || 'admin';
const password = process.env.TEST_PASSWORD || 'admin';

async function login(page: Page) {
  await page.goto('/login');
  await expect(page.locator('input[type="password"]')).toBeVisible();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: /login/i }).click();
  await expect(page.getByRole('heading', { name: /projects|create project/i }).first()).toBeVisible();
}

function collectJavaScriptErrors(page: Page) {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(message.text());
    }
  });
  page.on('pageerror', (error) => {
    errors.push(error.message);
  });
  return errors;
}

test('Login flow', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.locator('input[type="password"]')).toBeVisible();
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: /login/i }).click();

  await expect(page.getByRole('heading', { name: /projects|create project/i }).first()).toBeVisible();
  await expect(page).not.toHaveURL(/\/login$/);
  await page.screenshot({ path: testInfo.outputPath('01-login-success.png'), fullPage: true });
});

test('Projects page loads', async ({ page }, testInfo) => {
  const errors = collectJavaScriptErrors(page);

  await login(page);
  await expect(page.getByRole('heading', { name: /projects/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /create project/i })).toBeVisible();
  await expect(page.locator('.project-list')).toBeVisible();
  await expect.poll(() => errors, { message: 'No JavaScript console or page errors should be emitted' }).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('02-projects-page.png'), fullPage: true });
});

test('Navigation and page stability', async ({ page }, testInfo) => {
  const errors = collectJavaScriptErrors(page);

  await login(page);
  await page.goto('/projects/smoke-e2e-workspace');
  await expect(page.locator('.workspace')).toBeVisible();
  await expect(page.locator('.editor-pane')).toBeVisible();
  await expect(page.locator('.monaco-editor').first()).toBeVisible({ timeout: 20000 });
  await expect(page.getByText(/something went wrong/i)).toHaveCount(0);
  await expect(page.locator('.error-boundary')).toHaveCount(0);
  await expect.poll(() => errors, { message: 'No uncaught React or JavaScript errors should be emitted' }).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('03-workspace.png'), fullPage: true });
});

test('No-evidence guard', async ({ page }) => {
  await login(page);

  const chatInput = page.getByPlaceholder(/where is access control enforced/i);
  if (!(await chatInput.isVisible().catch(() => false))) {
    test.skip(true, 'No chat input is visible without a project loaded.');
  }

  const askButton = page.getByRole('button', { name: /ask/i });
  await expect(askButton).toBeDisabled();
});
