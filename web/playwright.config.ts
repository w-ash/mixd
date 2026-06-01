import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // auth-* runs under playwright-auth.config.ts. navigation/playlist-browse are
  // descoped from the CI gate until they mock the API via page.route() — they
  // currently assert on live backend data, which the Playwright CI container has
  // no backend to serve (ECONNREFUSED). Tracked in docs/backlog/unscheduled.md
  // ("E2E suite hardening"). visual.spec.ts (chromium) stays — it is the working
  // static visual-regression gate.
  testIgnore: /(auth-.+|navigation|playlist-browse)\.spec\.ts/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFileName}/{projectName}/{arg}{ext}",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.005,
      animations: "disabled",
      caret: "hide",
    },
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
      },
    },
    // iphone-15-pro descoped: its baselines were never captured/committed (only
    // chromium exists), so every mobile snapshot fails "snapshot doesn't exist".
    // Re-add with baselines generated in the pinned Playwright Docker image —
    // tracked in docs/backlog/unscheduled.md ("E2E suite hardening").
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
});
