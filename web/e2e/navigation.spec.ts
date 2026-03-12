import { expect, test } from "@playwright/test";

test.describe("App Navigation", () => {
  test("dashboard loads as home page", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL("/");
    // Dashboard should show library stats section
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("sidebar navigation between pages", async ({ page }) => {
    await page.goto("/");

    // Navigate to Playlists
    await page.getByRole("link", { name: /playlists/i }).click();
    await expect(page).toHaveURL("/playlists");

    // Navigate to Library
    await page.getByRole("link", { name: /library/i }).click();
    await expect(page).toHaveURL("/library");

    // Navigate to Workflows
    await page.getByRole("link", { name: /workflows/i }).click();
    await expect(page).toHaveURL("/workflows");

    // Navigate back to Dashboard
    await page.getByRole("link", { name: /dashboard/i }).first().click();
    await expect(page).toHaveURL("/");
  });

  test("unknown routes redirect to home", async ({ page }) => {
    await page.goto("/nonexistent-page");
    await expect(page).toHaveURL("/");
  });

  test("settings redirects to integrations", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).toHaveURL("/settings/integrations");
  });
});
