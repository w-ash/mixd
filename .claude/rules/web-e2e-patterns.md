---
paths:
  - "web/e2e/**"
---
# Web E2E Test Rules (Playwright)

E2E tests cover full user flows that span routing, auth, and rendering. Component-level behavior belongs in Vitest (`web/src/**/*.test.tsx`).

## Layout

- One `.spec.ts` file per user flow (`auth-smoke.spec.ts`, `navigation.spec.ts`, `playlist-browse.spec.ts`).
- Imports: `import { expect, test } from "@playwright/test";`
- Group related tests with `test.describe("Flow Name", () => { ... })`.
- Each `test()` exercises one user-visible behavior.

## Network mocking

E2E runs against a Vite dev server with no real backend — intercept via `page.route()` in `test.beforeEach`:

- **Pass through** Vite-served assets: documents, `/@`, `/node_modules/`, `/src/`, `__vite`, `favicon`, files matching `\.(ts|tsx|js|mjs|css|svg|png|html|woff2?)`.
- **Auth/session endpoints** → return 401 (unauthenticated) or 200 with a fixture (authenticated).
- **Other API calls** → 503 by default; fulfill specific routes earlier in the handler for the test scenario.

## Assertions

- Use semantic locators: `page.getByRole("link", { name: /playlists/i })`, `page.getByRole("heading", { level: 1 })`.
- Wait via `expect(page).toHaveURL(...)` and `await expect(locator).toBeVisible()` rather than `waitForTimeout`.
- Auth redirects need a generous timeout (`{ timeout: 15000 }`) — Neon Auth SDK does multiple round trips.

## When E2E vs Vitest

- **E2E** — routing, auth flows, multi-page navigation, layout integration, anything that needs a real browser.
- **Vitest** — component behavior, hooks, query mocking via MSW, isolated UI state. Default to Vitest unless the test genuinely needs the browser.

## Running

- `pnpm --prefix web test:e2e` — full Playwright suite.
- `pnpm --prefix web test:e2e:auth` — auth-smoke only (when iterating).
