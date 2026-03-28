import { expect, test } from "@playwright/test";

/**
 * Auth smoke tests.
 *
 * Run with: pnpm --prefix web test:e2e:auth
 *
 * These tests run against a Vite dev server with VITE_NEON_AUTH_URL set.
 * Since no real auth service or API backend is running, we intercept
 * non-asset requests and return controlled responses.
 */

test.beforeEach(async ({ page }) => {
  // Intercept all non-asset requests. The Neon Auth SDK and the API both
  // make requests that would hang without a backend. Static assets (JS, CSS,
  // fonts) are served by Vite and must pass through.
  await page.route("**/*", async (route) => {
    const url = route.request().url();

    // Page navigations and static assets served by Vite — pass through
    if (route.request().resourceType() === "document") {
      return route.continue();
    }
    if (url.includes("/@") || url.includes("/node_modules/") || url.includes("/src/")) {
      return route.continue();
    }
    if (/\.(ts|tsx|js|mjs|css|svg|png|html|woff2?)(\?|$)/.test(url)) {
      return route.continue();
    }
    if (url.includes("__vite") || url.includes("favicon")) {
      return route.continue();
    }

    // Auth/session endpoints → 401 (no valid session)
    if (url.includes("/auth/") || url.includes("/session")) {
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify(null),
      });
    }

    // Other API calls → 503 (no backend)
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "Service unavailable" }),
    });
  });
});

test.describe("Auth Smoke", () => {
  test("unauthenticated user is redirected to login page", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/auth\/sign-in/, { timeout: 15000 });
  });

  test("login page renders at /auth/sign-in", async ({ page }) => {
    await page.goto("/auth/sign-in");
    await expect(page).toHaveURL(/\/auth\/sign-in/);
  });

  test("sign-up page renders at /auth/sign-up", async ({ page }) => {
    await page.goto("/auth/sign-up");
    await expect(page).toHaveURL(/\/auth\/sign-up/);
  });

  test("login redirect alias works", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveURL(/\/auth\/sign-in/, { timeout: 15000 });
  });
});
