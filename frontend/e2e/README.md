# Playwright E2E Smoke Tests

Run from `frontend/`:

```powershell
npm run test:e2e
```

Prerequisites:

- Backend running on `http://localhost:8000`
- Frontend available on `http://localhost:5173`
- Playwright Chromium installed with `npx playwright install chromium --with-deps`

Environment variables:

- `TEST_USERNAME`, default `admin`
- `TEST_PASSWORD`, default `admin`

The smoke suite covers the critical thesis demo path:

- Login flow verifies the real authentication path and redirects to the projects page.
- Projects page load verifies the project creation/list shell renders without JavaScript errors and without seeded data.
- Navigation and stability verifies the workspace route renders and the Monaco editor container mounts without React crashes.
- No-evidence guard verifies the chat action is unavailable when there is no loaded, indexed project/evidence context. This test skips when no chat input is visible without a project.
