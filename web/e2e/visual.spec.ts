import { expect, type Page, test } from "@playwright/test";

/**
 * Visual-regression baselines for primary routes.
 *
 * Baselines under `web/e2e/__screenshots__/visual.spec.ts/` are
 * Linux-only — generated in the Playwright Docker image used by CI.
 * Local macOS PNGs won't match; regenerate via the Docker procedure
 * documented in `web/e2e/README.md`.
 */

// Routes that render without authentication or seeded data. Detail pages
// (`/library/:id`, `/playlists/:id`, `/workflows/:id`, run details) need
// fixture data and are deferred to a follow-up that adds a deterministic
// seed fixture under `web/e2e/fixtures/`.
const ROUTES = [
  { path: "/", slug: "dashboard" },
  { path: "/library", slug: "library" },
  { path: "/playlists", slug: "playlists" },
  { path: "/workflows", slug: "workflows" },
  { path: "/settings/integrations", slug: "settings-integrations" },
  { path: "/settings/sync", slug: "settings-sync" },
  { path: "/settings/tags", slug: "settings-tags" },
  { path: "/settings/imports", slug: "settings-imports" },
  { path: "/settings/account", slug: "settings-account" },
] as const;

const THEMES = ["light", "dark"] as const;

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
        await page.emulateMedia({ colorScheme: theme });
        await page.goto(route.path);
        await settleForScreenshot(page);
        await expect(page).toHaveScreenshot(`${route.slug}-${theme}.png`, {
          fullPage: true,
        });
      });
    }
  }
});
