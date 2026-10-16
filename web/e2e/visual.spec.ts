import { expect, type Page, type Route, test } from "@playwright/test";

import type {
  OperationRunListResponse,
  UserSettingsResponse,
} from "../src/api/generated/model";

/**
 * Visual-regression baselines for primary routes.
 *
 * Baselines under `web/e2e/__screenshots__/visual.spec.ts/` are
 * Linux/amd64-only — generated in the Playwright Docker image used by CI.
 * Local macOS PNGs, and arm64 renders from Docker on Apple Silicon, won't
 * match; regenerate via the Docker procedure documented in
 * `web/e2e/README.md`.
 *
 * Every `/api/v1/` request is answered by {@link mockApi}, so what a page
 * shows never depends on a backend or on how fast a failing request retries.
 */

// Routes that render without authentication or seeded data. Detail pages
// (`/library/:id`, `/playlists/:id`, `/workflows/:id`, run details) need
// fixture data and are deferred to a follow-up that adds a deterministic
// seed fixture under `web/e2e/fixtures/`.
const ROUTES = [
  { path: "/", slug: "dashboard" },
  { path: "/library", slug: "library" },
  { path: "/artists", slug: "artists" },
  { path: "/playlists", slug: "playlists" },
  { path: "/workflows", slug: "workflows" },
  { path: "/settings/integrations", slug: "settings-integrations" },
  { path: "/settings/sync", slug: "settings-sync" },
  { path: "/settings/tags", slug: "settings-tags" },
  { path: "/settings/imports", slug: "settings-imports" },
  { path: "/settings/account", slug: "settings-account" },
] as const;

const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];

/** Fixed "now", so relative timestamps render the same on every run. */
const FIXED_NOW = new Date("2026-06-01T12:00:00Z");

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Answer every backend call from this file.
 *
 * - `/settings` carries the theme: ThemeProvider adopts the server's
 *   `theme_mode` over its stored default (`dark`), so this response, not the
 *   media query, decides the theme.
 * - `/operation-runs` (the app-global poll) is an empty list, so the
 *   running-operations badge stays out of every shot.
 * - Anything else is a 404. `query-client` retries only `status >= 500`, so a
 *   page fails once and settles instead of churning through retries.
 */
async function mockApi(page: Page, theme: Theme) {
  await page.route("**/*", (route) => {
    const { pathname } = new URL(route.request().url());

    // Only `/api/v1/` is the backend. Vite serves the app's own source under
    // `/src/api/`, so a bare `/api/` match would hijack the module graph.
    if (!pathname.startsWith("/api/v1/")) return route.continue();

    if (pathname === "/api/v1/settings") {
      return json(route, 200, {
        theme_mode: theme,
      } satisfies UserSettingsResponse);
    }
    if (pathname.startsWith("/api/v1/operation-runs")) {
      return json(route, 200, {
        data: [],
        limit: 50,
        next_cursor: null,
      } satisfies OperationRunListResponse);
    }
    return json(route, 404, {
      error: { code: "NOT_FOUND", message: "No fixture for this endpoint" },
    });
  });
}

async function settleForScreenshot(page: Page) {
  await Promise.all([
    page.evaluate(() => document.fonts.ready),
    page.waitForLoadState("networkidle"),
  ]);
}

test.describe("Visual Regression", () => {
  for (const theme of THEMES) {
    for (const route of ROUTES) {
      test(`${route.slug} (${theme})`, async ({ page }) => {
        await mockApi(page, theme);
        await page.clock.setFixedTime(FIXED_NOW);
        await page.goto(route.path);
        await settleForScreenshot(page);
        await expect(page).toHaveScreenshot(`${route.slug}-${theme}.png`, {
          fullPage: true,
        });
      });
    }
  }
});
