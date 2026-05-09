import { expect, type Page, test } from "@playwright/test";

/**
 * Visual-regression baselines for primary routes.
 *
 * **Bootstrap state**: until initial baselines are generated and committed,
 * this suite is `describe.skip`. To bring online:
 *   1. Generate baselines inside the Playwright Docker image (see
 *      `web/e2e/README.md` for the exact command).
 *   2. Commit the PNGs under `web/e2e/__screenshots__/visual.spec.ts/`.
 *   3. Remove the `.skip` from the describe block below.
 *
 * Baselines are platform-locked to Linux (CI Docker) — local macOS PNGs
 * will not match. The suite is wired into CI via the `web-e2e` job; once
 * unskipped, layout drift fails the build.
 */

const ROUTES = [
  { path: "/", slug: "dashboard" },
  { path: "/playlists", slug: "playlists" },
  { path: "/workflows", slug: "workflows" },
] as const;

const THEMES = ["light", "dark"] as const;

async function settleForScreenshot(page: Page) {
  await Promise.all([
    page.evaluate(() => document.fonts.ready),
    page.waitForLoadState("networkidle"),
  ]);
}

test.describe.skip("Visual Regression", () => {
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
