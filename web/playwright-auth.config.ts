import { defineConfig, devices } from "@playwright/test";

/**
 * Auth-enabled Playwright config.
 *
 * Starts a Vite dev server with VITE_NEON_AUTH_URL set so the frontend
 * enables auth routing (AuthGuard, login page, etc.). The URL points to
 * a non-existent service — tests use page.route() to mock auth responses.
 *
 * Run: pnpm exec playwright test --config playwright-auth.config.ts
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /auth-.+\.spec\.ts/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:5175",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "auth",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command:
      "VITE_NEON_AUTH_URL=http://localhost:9999/auth pnpm exec vite --port 5175",
    url: "http://localhost:5175",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
