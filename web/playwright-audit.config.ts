import { defineConfig, devices } from "@playwright/test";

/**
 * Local visual-audit config. Runs only `*.audit.spec.ts` — the fixture-driven
 * Playlist Detail capture harness, which writes plain screenshots to
 * `web/e2e/__audit__/` for inspection. NOT a CI gate (the default config's
 * `testIgnore` excludes `.audit`); there are no `toHaveScreenshot` baselines.
 *
 * Run with: pnpm --prefix web test:e2e:audit
 *
 * Each spec sets its own viewport + colorScheme per state, so the single
 * chromium project below only supplies a baseURL and a default surface.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /\.audit\.spec\.ts$/,
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
  },
});
