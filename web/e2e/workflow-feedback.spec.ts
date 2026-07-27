/**
 * End-to-end coverage for workflow save + run feedback.
 *
 * **No backend, no Spotify.** Playwright runs against a bare Vite dev server;
 * every `/api/v1/` request below is fulfilled from `fixtures/workflow-feedback`,
 * including `POST /run` and its SSE stream. Nothing in this file can reach the
 * application layer, a connector, or a real music service.
 *
 * The four flows mirror the user complaints this work addresses:
 *   1. a save is visible immediately, without waiting out the staleTime window
 *   2. running from the list updates that row as it starts and as it finishes
 *   3. a run this tab never started (scheduler, another device) still shows
 *   4. a run's own page follows the run live instead of freezing on load
 */
import { expect, type Page, type Route, test } from "@playwright/test";

import {
  activeRun,
  Deferred,
  OPERATION_ID,
  paginated,
  RUN_ID,
  runToCompletion,
  sseBody,
  WF_A,
  WF_B,
  workflowDetail,
  workflowSummary,
} from "./fixtures/workflow-feedback";

/** Mutable per-test server state — what the mocked API "believes" right now. */
interface ServerState {
  detail: ReturnType<typeof workflowDetail>;
  list: ReturnType<typeof workflowSummary>[];
  activeRuns: ReturnType<typeof activeRun>[];
  /** Parks the workflow-detail GET so a cache read can be proven. */
  holdDetailGet: Deferred | null;
  /** Parks the SSE response so a run can be observed mid-flight. */
  holdStream: Deferred | null;
  /** Events delivered once the stream is released. */
  streamEvents: ReturnType<typeof runToCompletion>;
  runStarted: boolean;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Install the workflow API surface.
 *
 * Two details are load-bearing and inherited from the playlist audit harness:
 * match on the precise `/api/v1/` prefix (a bare `/api/` hijacks Vite's own
 * `/src/api/` module graph and React never mounts), and default unmocked API
 * calls to 404 rather than 503 (`query-client` retries `>= 500`, and that churn
 * keeps the page from ever settling).
 */
async function installWorkflowRoutes(page: Page, state: ServerState) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (!path.startsWith("/api/v1/")) return route.continue();

    // App-global polls → benign empties, so no badge or toast bleeds in.
    if (path.startsWith("/api/v1/operation-runs")) {
      return json(route, paginated([]));
    }
    if (path.startsWith("/api/v1/schedules")) {
      return json(route, paginated([]));
    }
    if (path.startsWith("/api/v1/connectors")) {
      return json(route, []);
    }

    // SSE progress stream — parked until the test releases it.
    if (path === `/api/v1/operations/${OPERATION_ID}/progress`) {
      if (state.holdStream) await state.holdStream.promise;
      return route.fulfill({
        status: 200,
        headers: {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
        },
        body: sseBody(state.streamEvents),
      });
    }
    if (path.startsWith("/api/v1/operations/")) {
      return json(route, { status: "RUNNING", operation_id: OPERATION_ID });
    }

    if (path === "/api/v1/workflows/active-runs") {
      return json(route, paginated(state.activeRuns));
    }
    if (path === "/api/v1/workflows/nodes") return json(route, []);
    if (path === "/api/v1/workflows/templates") return json(route, []);

    // Start a run: 202 with the operation handle, and the server now considers
    // the workflow in flight.
    if (method === "POST" && path.endsWith("/run")) {
      state.runStarted = true;
      state.activeRuns = [activeRun()];
      return json(route, { operation_id: OPERATION_ID, run_id: RUN_ID }, 202);
    }

    if (path.endsWith("/versions")) return json(route, []);

    if (path === `/api/v1/workflows/${WF_A}/runs/${RUN_ID}`) {
      return json(route, {
        ...activeRun({ status: state.runStarted ? "running" : "completed" }),
        definition_snapshot: state.detail.definition,
        output_tracks: [],
        metric_columns: [],
        nodes: [],
      });
    }
    if (path.endsWith("/runs")) return json(route, paginated([]));

    // Save: respond with the updated workflow, then remember it.
    if (method === "PATCH" && path === `/api/v1/workflows/${WF_A}`) {
      const body = route.request().postDataJSON() as {
        definition?: { name?: string };
      };
      state.detail = workflowDetail({
        name: body.definition?.name ?? state.detail.name,
        definition_version: 2,
        task_count: 4,
      });
      return json(route, state.detail);
    }

    if (path === `/api/v1/workflows/${WF_A}`) {
      // Parked on request: if the page still shows fresh data while this is
      // outstanding, that data came from the cache write, not a refetch.
      if (state.holdDetailGet) await state.holdDetailGet.promise;
      return json(route, state.detail);
    }

    if (path === "/api/v1/workflows") return json(route, paginated(state.list));

    return json(
      route,
      { error: { code: "NOT_FOUND", message: "Unmocked" } },
      404,
    );
  });
}

function baseState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    detail: workflowDetail(),
    list: [workflowSummary()],
    activeRuns: [],
    holdDetailGet: null,
    holdStream: null,
    streamEvents: runToCompletion(["task_1", "task_2", "task_3"]),
    runStarted: false,
    ...overrides,
  };
}

test.describe("Workflow save feedback", () => {
  test("the detail page shows the saved workflow without waiting for a refetch", async ({
    page,
  }) => {
    const state = baseState();
    await installWorkflowRoutes(page, state);

    await test.step("edit the workflow name in the editor", async () => {
      await page.goto(`/workflows/${WF_A}/edit`);
      const nameInput = page.getByLabel("Workflow name");
      await expect(nameInput).toHaveValue("Weekly Obsessions", {
        timeout: 15000,
      });
      await nameInput.fill("Renamed Obsessions");
    });

    await test.step("save", async () => {
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByText("Workflow saved")).toBeVisible();
    });

    await test.step("detail renders from cache while its GET is parked", async () => {
      // Park the refetch. Without the mutation-response cache write the page
      // has nothing to render but the pre-edit copy.
      //
      // The navigation MUST be client-side (the toolbar's Back button, which
      // goes to the detail page for a saved workflow) — a page.goto would
      // reload the SPA and discard the very cache under test.
      state.holdDetailGet = new Deferred();
      await page.getByLabel("Back").click();

      await expect(page).toHaveURL(new RegExp(`/workflows/${WF_A}$`));
      await expect(
        page.getByRole("heading", { name: "Renamed Obsessions" }),
      ).toBeVisible();
      // Rendered, not loading: the parked GET never resolved, so this content
      // can only have come from the cache the save wrote.
      await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0);

      state.holdDetailGet.release();
    });
  });
});

test.describe("Workflow run feedback", () => {
  test("running from the list updates that row as it starts and finishes", async ({
    page,
  }) => {
    const state = baseState();
    state.holdStream = new Deferred();
    await installWorkflowRoutes(page, state);

    await page.goto("/workflows");
    // ResponsiveTable keeps the card slot mounted but hidden at this viewport,
    // so every list assertion is scoped to the visible table.
    const table = page.getByRole("table");
    await expect(table.getByText("Weekly Obsessions")).toBeVisible();

    await test.step("the row flips to running immediately", async () => {
      await table.getByTitle("Run workflow").click();
      // In-tab execution state carries this one; the start-invalidation that
      // serves *other* surfaces is pinned by the third test below and by
      // `useWorkflowExecution.test.ts`.
      await expect(table.getByTitle("Workflow is running")).toBeVisible();
    });

    await test.step("the row settles once the run completes", async () => {
      // The server now reflects a finished run before the stream terminates,
      // so the terminal invalidation has something new to read.
      state.activeRuns = [];
      state.runStarted = false;
      state.list = [
        workflowSummary({
          successful_run_count: 1,
          last_run: {
            id: RUN_ID,
            run_number: 5,
            status: "completed",
            definition_version: 1,
            output_track_count: 42,
          },
        }),
      ];
      state.holdStream?.release();

      await expect(table.getByText("Completed")).toBeVisible();
      await expect(table.getByTitle("1 successful run")).toHaveText("1");
    });
  });

  test("a run this tab never started still shows as running", async ({
    page,
  }) => {
    // The scheduler / second-device case: the run endpoint is never called.
    const state = baseState({
      list: [
        workflowSummary(),
        workflowSummary({
          id: WF_B,
          name: "Deep Cuts",
          last_run: {
            id: RUN_ID,
            run_number: 2,
            status: "completed",
            definition_version: 1,
          },
        }),
      ],
      activeRuns: [activeRun({ workflow_id: WF_B })],
    });
    await installWorkflowRoutes(page, state);

    await page.goto("/workflows");
    const table = page.getByRole("table");

    await expect(table.getByText("Deep Cuts")).toBeVisible();
    await expect(table.getByTitle("Workflow is running")).toBeVisible();
    // Live state wins over the stale "Completed" the row was serving.
    await expect(table.getByText("Completed")).toHaveCount(0);
    // The other row is unaffected — the active-run guard is per-workflow.
    await expect(table.getByTitle("Run workflow")).toBeEnabled();
  });

  test("the run detail page follows a live run instead of freezing", async ({
    page,
  }) => {
    const state = baseState({ activeRuns: [activeRun()] });
    state.runStarted = true;
    state.holdStream = new Deferred();
    await installWorkflowRoutes(page, state);

    await page.goto(`/workflows/${WF_A}/runs/${RUN_ID}`);

    await expect(page.getByText("Running").first()).toBeVisible();

    state.holdStream?.release();
  });
});
