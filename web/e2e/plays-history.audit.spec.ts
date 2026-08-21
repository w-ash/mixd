/**
 * v0.10.4 play-history surfaces — visual-audit capture harness.
 *
 * NOT a CI gate. Drives `/library/plays`, the Library play columns/filters,
 * and Track Detail's Play History section through their states and writes
 * plain screenshots to `web/e2e/__audit__/` for side-by-side review.
 *
 * Run: pnpm --prefix web test:e2e:audit
 * Output: web/e2e/__audit__/plays-<slug>-<viewport>-<theme>.png
 */
import { type Page, test } from "@playwright/test";

import {
  type EndpointMocks,
  FIXED_NOW,
  FIXTURE_TRACK_ID,
  installPlaysAuditRoutes,
  libraryStates,
  playsStates,
  trackDetailStates,
} from "./fixtures/plays-history";

const OUT = "e2e/__audit__";

const SHOT = { animations: "disabled", caret: "hide" } as const;

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
] as const;
const THEMES = ["light", "dark"] as const;

type SettleMode = "idle" | "skeleton";

async function settle(page: Page, mode: SettleMode) {
  if (mode === "skeleton") {
    await page.locator('[class*="animate-shimmer"]').first().waitFor();
  } else {
    await page.waitForLoadState("networkidle");
  }
  await page.evaluate(() => document.fonts.ready);
}

interface Scenario {
  slug: string;
  url: string;
  mocks: EndpointMocks;
  mode?: SettleMode;
  /** Optional interaction after settle, before capture. */
  act?: (page: Page) => Promise<void>;
  /** Restrict to one viewport (e.g. mobile-only sheet). */
  only?: "desktop" | "mobile";
}

const SCENARIOS: Scenario[] = [
  {
    slug: "feed-populated",
    url: "/library/plays",
    mocks: playsStates.populated(),
  },
  { slug: "feed-empty", url: "/library/plays", mocks: playsStates.empty() },
  {
    slug: "feed-loading",
    url: "/library/plays",
    mocks: playsStates.loading(),
    mode: "skeleton",
  },
  {
    slug: "feed-range-chip",
    url: "/library/plays?from=2026-07-01T00%3A00%3A00Z&to=2026-07-08T00%3A00%3A00Z",
    mocks: playsStates.populated(),
  },
  {
    slug: "library-play-columns",
    url: "/library",
    mocks: libraryStates.populated(),
  },
  {
    slug: "library-filters-open",
    url: "/library",
    mocks: libraryStates.populated(),
    act: async (page) => {
      await page.getByRole("button", { name: /^Filters/ }).click();
      await page
        .locator('#library-filter-panel[data-state="open"]')
        .waitFor({ timeout: 5000 })
        .catch(async () => {
          // Mobile renders a Sheet instead of the inline disclosure.
          await page.getByRole("dialog").waitFor({ timeout: 5000 });
        });
      await page.getByLabel("Filter by play count").waitFor();
      // Let the 150ms max-height expand finish before capture.
      await page.waitForTimeout(300);
    },
  },
  {
    slug: "track-detail-history",
    url: `/library/${FIXTURE_TRACK_ID}`,
    mocks: trackDetailStates.manyPlays(),
  },
  {
    slug: "track-detail-few-plays",
    url: `/library/${FIXTURE_TRACK_ID}`,
    mocks: trackDetailStates.fewPlays(),
  },
  {
    slug: "nav-more-sheet",
    url: "/library/plays",
    mocks: playsStates.populated(),
    only: "mobile",
    act: async (page) => {
      await page.getByRole("button", { name: /more/i }).click();
      await page.getByText("Plays", { exact: true }).first().waitFor();
    },
  },
];

test.describe("Play history — visual audit", () => {
  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      for (const sc of SCENARIOS) {
        if (sc.only && sc.only !== vp.name) continue;
        test(`plays-${sc.slug} (${vp.name}/${theme})`, async ({ page }) => {
          await page.setViewportSize({ width: vp.width, height: vp.height });
          // ThemeContext defaults to a STORED mode ("dark" when unset), so
          // emulateMedia alone can't flip it — seed the stored preference.
          await page.addInitScript(
            (t) => localStorage.setItem("mixd-theme", t),
            theme,
          );
          await page.emulateMedia({ colorScheme: theme });
          await page.clock.setFixedTime(FIXED_NOW);
          await installPlaysAuditRoutes(page, sc.mocks);
          await page.goto(sc.url);
          await settle(page, sc.mode ?? "idle");
          await sc.act?.(page);
          await page.screenshot({
            path: `${OUT}/plays-${sc.slug}-${vp.name}-${theme}.png`,
            fullPage: true,
            ...SHOT,
          });
        });
      }
    }
  }
});
