---
paths:
  - "web/e2e/**"
  - "web/playwright.config.ts"
  - "web/playwright-auth.config.ts"
---
# Web E2E Test Rules (Playwright)

E2E covers full user flows that span routing, auth, and rendering. Component-level behavior belongs in Vitest (`web/src/**/*.test.tsx`).

## Configs

- **`playwright.config.ts`** (default) — runs without auth. Used by `pnpm test:e2e` and the `web-e2e` CI job. Excludes `auth-*`, `navigation`, `playlist-browse`, and `*.audit` specs via `testIgnore`.
- **`playwright-auth.config.ts`** — runs with auth enabled, mocked via `page.route()`. Used by `pnpm test:e2e:auth`.
- **`playwright-audit.config.ts`** — runs only `*.audit.spec.ts` (local visual-audit harnesses, see below). Used by `pnpm test:e2e:audit`. Never runs in CI.

## Layout

- One `.spec.ts` file per user flow (`auth-smoke.spec.ts`, `navigation.spec.ts`, `playlist-browse.spec.ts`, `visual.spec.ts`).
- Imports: `import { expect, test } from "@playwright/test";`
- Group related tests with `test.describe("Flow Name", () => { ... })`.
- For multi-action flows (filter → apply → verify), wrap each phase in `await test.step("phase name", async () => { ... })` so failures point at the failing phase.

## Network mocking

E2E runs against a Vite dev server with no real backend. Use `page.route()` for single-page interception (`browserContext.route()` only when popups need the same handler). The pass-through filter pattern is canonical in `auth-smoke.spec.ts`:
- Static assets (Vite-served) pass through.
- Auth/session endpoints → 401 (or 200 with fixture in auth tests).
- Other API calls → 503 by default; fulfill specific routes earlier in the handler for the test scenario.
- For mocking shared across many tests, prefer a custom fixture via `test.extend()` over duplicating `test.beforeEach` blocks.

## Visual regression

`visual.spec.ts` baselines each route × theme via `toHaveScreenshot`. Snapshots live under `web/e2e/__screenshots__/visual.spec.ts/`.

- **Baselines are Linux-only**: generated in `mcr.microsoft.com/playwright:v1.59.1-noble`. Local macOS PNGs won't match. Regenerate via the Docker procedure in `web/e2e/README.md`.
- Settle deterministically before capture: `await Promise.all([page.evaluate(() => document.fonts.ready), page.waitForLoadState("networkidle")])`.
- Diff config (in `playwright.config.ts`): `maxDiffPixelRatio: 0.005`, `animations: "disabled"`, `caret: "hide"`.
- Mask dynamic content (relative timestamps, avatars) at the locator level when adding new pages.
- The blanket `*.png` rule in `.gitignore` is overridden by `!web/e2e/__screenshots__/**/*.png` so baselines commit.

## Visual-audit harness (full-state inventory)

Distinct from `visual.spec.ts` (a CI baseline gate): `*.audit.spec.ts` are **local inspection tools**. They drive one page into *every* state via route-mocked fixtures and write plain `page.screenshot()`s to the gitignored `web/e2e/__audit__/` for a human/agent to review side by side — **no `toHaveScreenshot` baselines** (so no Docker/Linux dependency), no assertions. Run via `pnpm --prefix web test:e2e:audit`. This is the tool that un-defers the detail pages `visual.spec.ts` skipped (`/library/:id`, `/workflows/:id`, run details).

Worked example: **Playlist Detail** — `playlist-detail-states.audit.spec.ts` + `web/e2e/fixtures/{playlist-detail,route-mock}.ts`. To audit another page, write per-state fixture factories like `fixtures/playlist-detail.ts` and reuse `installPlaylistDetailRoutes` from `fixtures/route-mock.ts`. Load-bearing details:

- **Type the fixtures against the generated model** (`import type … from "../../src/api/generated/model"`) so schema drift is a `tsc` error. A sibling Vitest test in `src/` that imports the fixtures pulls them into the type program (`tsconfig` only includes `src/`).
- **Route-mock gotcha:** key the API branch on the precise **`/api/v1/`** prefix, *not* a bare `/api/` — Vite serves the app's own source under `/src/api/`, so a bare match hijacks the module graph and React never mounts (and a blank screenshot still "passes"). Default unmocked `/api/v1/` calls to **404, not 503**: `query-client` retries `status >= 500`, and that churn never lets `networkidle` settle.
- **Determinism:** `await page.clock.setFixedTime(new Date(...))` *before* `goto` so relative timestamps ("4d ago") are stable; pass `{ animations: "disabled", caret: "hide" }` to `page.screenshot()` (plain screenshots, unlike `toHaveScreenshot`, don't freeze CSS animations or hide the caret).
- **Theme + viewport:** `emulateMedia({ colorScheme })` flips the theme (ThemeContext defaults to `system`); loop `{mobile, desktop} × {light, dark}` with `setViewportSize`. Full-page for composition, a dialog/section locator for detail.
- Then **read the PNGs and audit each against `web-design-system.md`** — fix what's indefensible. `__audit__/*.png` is auto-gitignored by the blanket `*.png` rule.

## Assertions

- Use semantic locators: `page.getByRole("link", { name: /playlists/i })`, `page.getByRole("heading", { level: 1 })`.
- Wait via `expect(page).toHaveURL(/regex/)` and `await expect(locator).toBeVisible()`.
- Auth redirects need a generous timeout (`{ timeout: 15000 }`) — Neon Auth SDK does multiple round trips.

## Anti-patterns

- `page.waitForTimeout(n)` — flaky and masks real issues. Use `expect(locator).toBeVisible()` or `locator.waitFor()`.
- `page.waitForSelector(...)` — superseded by `locator.waitFor({ state: "visible" })`.
- Hardcoded URL strings in `toHaveURL` — prefer regex (`/\/auth\/sign-in/`) so trailing slashes and query params don't break the assertion.
- `trace: "on"` — too much storage. Mixd uses `trace: "on-first-retry"`; keep it that way.
- Locally-generated visual baselines — Linux/macOS font rendering differs and produces noise on every PR. CI Docker is the only acceptable source.
- Full-page screenshots when a component-level region would suffice — slower diffs and harder to read.

## When E2E vs Vitest

- **E2E** — routing, auth flows, multi-page navigation, layout integration, anything that needs a real browser.
- **Vitest** — component behavior, hooks, query mocking via MSW, isolated UI state. Default to Vitest unless the test genuinely needs a browser.

## Running

- `pnpm --prefix web test:e2e` — full Playwright suite (default config).
- `pnpm --prefix web test:e2e:auth` — auth-smoke only (when iterating).
- `pnpm --prefix web test:e2e:audit` — visual-audit harnesses → screenshots in `web/e2e/__audit__/` (local only, never CI).
- `web-e2e` GitHub Actions job runs the full suite in the pinned Playwright Docker image on every PR; visual diffs land in `playwright-report/` artifacts.
