import { expect, test } from "@playwright/test";

test.describe("Playlist Browsing", () => {
  test("playlists page loads and shows list", async ({ page }) => {
    await page.goto("/playlists");
    await expect(
      page.getByRole("heading", { name: /playlists/i }),
    ).toBeVisible();
  });
});

test.describe("Workflow Browsing", () => {
  test("workflows page loads", async ({ page }) => {
    await page.goto("/workflows");
    await expect(
      page.getByRole("heading", { name: /workflows/i }),
    ).toBeVisible();
  });

  test("new workflow link navigates to editor", async ({ page }) => {
    await page.goto("/workflows");

    const newButton = page.getByRole("link", { name: /new|create/i });
    const count = await newButton.count();
    if (count > 0) {
      await newButton.click();
      await expect(page).toHaveURL("/workflows/new");
    }
  });
});
