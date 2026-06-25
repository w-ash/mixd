/**
 * Playlist Detail — visual-audit capture harness.
 *
 * NOT a CI gate. This is an inspection tool: it drives Playlist Detail into
 * every state via route-mocked fixtures and writes plain region screenshots to
 * `web/e2e/__audit__/` (gitignored) for a human / Claude to review side by side.
 * It makes no assertions and uses no `toHaveScreenshot` baselines — the `.audit`
 * suffix excludes it from the CI run (see `playwright.config.ts` testIgnore).
 *
 * Run locally (the default config's testIgnore excludes `.audit`, so use the
 * dedicated audit config):
 *   pnpm --prefix web test:e2e:audit
 * Output: web/e2e/__audit__/<slug>-<viewport>-<theme>.png
 */
import { type Page, test } from "@playwright/test";

import {
  dialogStates,
  type EndpointMocks,
  FIXTURE_PLAYLIST_ID,
  linkStates,
  pageStates,
  syncDialogStates,
  trackStates,
} from "./fixtures/playlist-detail";
import { installPlaylistDetailRoutes } from "./fixtures/route-mock";

const OUT = "e2e/__audit__";
const URL = `/playlists/${FIXTURE_PLAYLIST_ID}`;

// Freeze "now" so relative timestamps ("4d ago", "Updated …") render
// deterministically run-to-run. setFixedTime (not install) fixes Date.now()
// without pausing timers/polling. Must be set before goto.
const FIXED_NOW = new Date("2026-06-25T12:00:00Z");

// Plain page.screenshot() (unlike toHaveScreenshot) doesn't freeze CSS
// animations or hide the caret — opt in so the shimmer/fade-up and text
// cursor can't land mid-frame.
const SHOT = { animations: "disabled", caret: "hide" } as const;

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
] as const;
const THEMES = ["light", "dark"] as const;

/** How to wait before capturing: full settle, or a partial wait for skeletons. */
type SettleMode = "idle" | "skeleton" | "preview-loading";

async function settle(page: Page, mode: SettleMode) {
  if (mode === "skeleton") {
    await page.locator('[class*="animate-shimmer"]').first().waitFor();
  } else if (mode === "preview-loading") {
    await page.getByText("Loading preview").waitFor();
  } else {
    await page.waitForLoadState("networkidle");
  }
  await page.evaluate(() => document.fonts.ready);
}

interface PageScenario {
  slug: string;
  mocks: EndpointMocks;
  mode?: SettleMode;
}

interface DialogScenario extends PageScenario {
  /** Opens the dialog from the loaded page; returns once it's interactable. */
  open: (page: Page) => Promise<void>;
}

// ─── Page / section scenarios (full-page capture) ────────────────────────────

const PAGE_SCENARIOS: PageScenario[] = [
  { slug: "page-loading", mocks: pageStates.loading(), mode: "skeleton" },
  { slug: "page-error", mocks: pageStates.error() },
  { slug: "page-success", mocks: pageStates.success() },
  { slug: "page-empty", mocks: pageStates.emptyPlaylist() },
  {
    slug: "page-tracks-loading",
    mocks: pageStates.tracksLoading(),
    mode: "skeleton",
  },
  { slug: "page-long-name", mocks: pageStates.longName() },
  { slug: "tracks-unresolved", mocks: trackStates.withUnresolved() },
  { slug: "links-none", mocks: linkStates.none() },
  { slug: "links-never-synced-pull", mocks: linkStates.neverSyncedPull() },
  { slug: "links-never-synced-push", mocks: linkStates.neverSyncedPush() },
  { slug: "links-synced", mocks: linkStates.synced() },
  { slug: "links-syncing", mocks: linkStates.syncing() },
  { slug: "links-error", mocks: linkStates.error() },
  { slug: "links-unmatched", mocks: linkStates.withUnmatched() },
  { slug: "links-multiple", mocks: linkStates.multiple() },
];

// ─── Dialog scenarios (dialog-region capture) ────────────────────────────────

const openByName = (name: string | RegExp) => async (page: Page) => {
  await page.getByRole("button", { name }).first().click();
  await page.getByRole("dialog").waitFor();
};

/** Open the row's Sync dialog and let the preview settle (unless it's pending). */
const openSync = (mode: SettleMode) => async (page: Page) => {
  await page.getByRole("button", { name: /^sync/i }).first().click();
  await page.getByRole("dialog").waitFor();
  await settle(page, mode);
};

const DIALOG_SCENARIOS: DialogScenario[] = [
  { slug: "dialog-edit", mocks: dialogStates.base(), open: openByName("Edit") },
  {
    slug: "dialog-delete",
    mocks: dialogStates.base(),
    open: openByName("Delete"),
  },
  {
    slug: "dialog-link",
    mocks: dialogStates.base(),
    open: openByName("Link Playlist"),
  },
  {
    slug: "sync-first",
    mocks: syncDialogStates.firstSync(),
    open: openSync("idle"),
  },
  {
    slug: "sync-non-destructive",
    mocks: syncDialogStates.nonDestructive(),
    open: openSync("idle"),
  },
  {
    slug: "sync-destructive",
    mocks: syncDialogStates.destructive(),
    open: openSync("idle"),
  },
  { slug: "sync-noop", mocks: syncDialogStates.noop(), open: openSync("idle") },
  {
    slug: "sync-preview-loading",
    mocks: syncDialogStates.previewLoading(),
    open: openSync("preview-loading"),
  },
  {
    slug: "sync-preview-error",
    mocks: syncDialogStates.previewError(),
    open: openSync("idle"),
  },
];

// ─── Capture ─────────────────────────────────────────────────────────────────

test.describe("Playlist Detail — visual audit", () => {
  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      for (const sc of PAGE_SCENARIOS) {
        test(`${sc.slug} (${vp.name}/${theme})`, async ({ page }) => {
          await page.setViewportSize({ width: vp.width, height: vp.height });
          await page.emulateMedia({ colorScheme: theme });
          await page.clock.setFixedTime(FIXED_NOW);
          await installPlaylistDetailRoutes(page, sc.mocks);
          await page.goto(URL);
          await settle(page, sc.mode ?? "idle");
          await page.screenshot({
            path: `${OUT}/${sc.slug}-${vp.name}-${theme}.png`,
            fullPage: true,
            ...SHOT,
          });
        });
      }

      for (const sc of DIALOG_SCENARIOS) {
        test(`${sc.slug} (${vp.name}/${theme})`, async ({ page }) => {
          await page.setViewportSize({ width: vp.width, height: vp.height });
          await page.emulateMedia({ colorScheme: theme });
          await page.clock.setFixedTime(FIXED_NOW);
          await installPlaylistDetailRoutes(page, sc.mocks);
          await page.goto(URL);
          await settle(page, "idle");
          await sc.open(page);
          await page.getByRole("dialog").screenshot({
            path: `${OUT}/${sc.slug}-${vp.name}-${theme}.png`,
            ...SHOT,
          });
        });
      }
    }
  }
});
