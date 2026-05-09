import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: /auth-.+\.spec\.ts/,
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
    {
      name: "iphone-15-pro",
      // Real iPhone 15 Pro: 393×852 CSS, devicePixelRatio 3, mobile UA.
      use: {
        ...devices["iPhone 15 Pro"],
      },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
  },
});
