/**
 * Spotify export import queue — visual-audit capture harness.
 *
 * NOT a CI gate. Drives the Sync page's export card into each queue state via
 * route-mocked fixtures and writes region screenshots to `web/e2e/__audit__/`
 * (gitignored) for review. No assertions, no baselines; the `.audit` suffix
 * keeps it out of the CI run.
 *
 *   pnpm --prefix web test:e2e:audit
 */
import { type Page, test } from "@playwright/test";

const OUT = "e2e/__audit__";
const FIXED_NOW = new Date("2026-08-09T10:30:00Z");
const SHOT = { animations: "disabled", caret: "hide" } as const;

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 390, height: 900 },
] as const;
const THEMES = ["light", "dark"] as const;

interface Entry {
  filename: string;
  position: number;
  status: string;
  operation_id: string | null;
  run_id: string | null;
  size_bytes: number;
  started_at: string | null;
  settled_at: string | null;
  counts: Record<string, unknown> | null;
}

const at = (minute: number) =>
  new Date(Date.UTC(2026, 7, 9, 10, minute)).toISOString();

function done(position: number, minute: number, plays: number): Entry {
  return {
    filename: `Streaming_History_Audio_20${14 + position}_${position}.json`,
    position,
    status: "complete",
    operation_id: `op-${position}`,
    run_id: `run-${position}`,
    size_bytes: 4_000_000,
    started_at: at(minute),
    settled_at: at(minute + 3),
    counts: { track_plays: plays },
  };
}

function queued(position: number): Entry {
  return {
    filename: `Streaming_History_Audio_20${14 + position}_${position}.json`,
    position,
    status: "queued",
    operation_id: null,
    run_id: null,
    size_bytes: 4_000_000,
    started_at: null,
    settled_at: null,
    counts: null,
  };
}

const MID_DRAIN: Entry[] = [
  done(0, 0, 14_208),
  done(1, 3, 11_940),
  {
    ...queued(2),
    status: "error",
    operation_id: "op-2",
    run_id: "run-2",
    started_at: at(6),
    settled_at: at(6),
    counts: { error_message: "malformed JSON at line 8814" },
  },
  {
    ...queued(3),
    status: "running",
    operation_id: "op-3",
    run_id: "run-3",
    started_at: at(6),
  },
  queued(4),
  queued(5),
];

const DRAINED: Entry[] = MID_DRAIN.map((entry) =>
  entry.status === "running"
    ? {
        ...entry,
        status: "complete",
        settled_at: at(9),
        counts: { track_plays: 9_120 },
      }
    : entry.status === "queued"
      ? {
          ...entry,
          status: "complete",
          started_at: at(9),
          settled_at: at(12),
          counts: { track_plays: 7_400 },
        }
      : entry,
);

const SCENARIOS: Array<{ slug: string; entries: Entry[] | null }> = [
  { slug: "queue-none", entries: null },
  { slug: "queue-fresh", entries: [queued(0), queued(1), queued(2)] },
  { slug: "queue-mid-drain", entries: MID_DRAIN },
  { slug: "queue-drained-with-failure", entries: DRAINED },
];

async function installRoutes(page: Page, entries: Entry[] | null) {
  await page.route("**/*", (route) => {
    const url = route.request().url();
    // MUST be the precise /api/v1/ prefix: Vite serves the app's own source
    // under /src/api/, and a bare "/api/" match hijacks the module graph so
    // React never mounts — and a blank screenshot still "passes".
    if (!url.includes("/api/v1/")) return route.continue();

    const json = (status: number, body: unknown) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (url.includes("/imports/spotify/history/queue")) {
      return entries === null
        ? json(404, {
            error: { code: "NOT_FOUND", message: "No import queue" },
          })
        : json(200, {
            queue_id: "q-1",
            operation_id: "drain-op",
            started_at: at(0),
            entries,
          });
    }
    if (url.includes("/imports/checkpoints")) return json(200, []);
    if (url.includes("/play-polling"))
      return json(200, { service: "spotify", enabled: false });
    if (url.includes("/connectors")) return json(200, []);
    if (url.includes("/operation-runs"))
      return json(200, { data: [], total: 0 });
    // 404 rather than 503: query-client retries >= 500, and that churn would
    // stop `networkidle` ever settling.
    return json(404, { error: { code: "NOT_FOUND", message: "no fixture" } });
  });
}

for (const viewport of VIEWPORTS) {
  for (const theme of THEMES) {
    test.describe(`${viewport.name} · ${theme}`, () => {
      test.use({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
      });

      for (const scenario of SCENARIOS) {
        test(scenario.slug, async ({ page }) => {
          await page.clock.setFixedTime(FIXED_NOW);
          await installRoutes(page, scenario.entries);
          await page.goto("/settings/sync");
          await page.waitForLoadState("networkidle");
          await page.evaluate(() => document.fonts.ready);

          const card = page
            .getByText("Spotify Data Export")
            .locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
          await card.waitFor();
          await card.screenshot({
            path: `${OUT}/sync-${scenario.slug}-${viewport.name}-${theme}.png`,
            ...SHOT,
          });
        });
      }
    });
  }
}
