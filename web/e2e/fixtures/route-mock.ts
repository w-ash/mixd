/**
 * Route-mock installer for the Playlist Detail audit harness.
 *
 * Mirrors the pass-through filter from `auth-smoke.spec.ts`: Vite-served assets
 * and document navigations continue to the dev server; API calls are fulfilled
 * from the supplied {@link EndpointMocks}. Two deliberate defaults:
 *
 *  - the app-global `/operation-runs` poll is force-answered with an empty list,
 *    so the OperationsProvider badge/toasts never bleed into a screenshot;
 *  - any other unmocked `/api/` call returns **404, not 503** — `query-client`
 *    retries `status >= 500`, and that retry churn would keep `networkidle` from
 *    ever settling. A 404 throws once, silently, with no retry.
 *
 * Generic enough to reuse for the other deferred detail pages later.
 */
import type { Page, Route } from "@playwright/test";

import {
  type EndpointMocks,
  FIXTURE_LINK_ID,
  FIXTURE_PLAYLIST_ID,
  type MockResponse,
} from "./playlist-detail";

function fulfillJson(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Serve one endpoint's canned response; a missing fixture → silent 404. */
function apply(route: Route, res: MockResponse | undefined): Promise<void> {
  if (!res) {
    return fulfillJson(route, 404, {
      error: { code: "NOT_FOUND", message: "No fixture for this endpoint" },
    });
  }
  switch (res.kind) {
    case "pending":
      // Never resolve: the request hangs so the loading skeleton stays on screen.
      return new Promise<void>(() => {});
    case "error":
      return fulfillJson(route, res.status, {
        error: { code: res.code, message: res.message },
      });
    case "json":
      return fulfillJson(route, res.status, res.body);
  }
}

export async function installPlaylistDetailRoutes(
  page: Page,
  mocks: EndpointMocks = {},
): Promise<void> {
  const base = `/api/v1/playlists/${FIXTURE_PLAYLIST_ID}`;

  await page.route("**/*", (route) => {
    const url = route.request().url();

    // Pass everything that isn't a real backend call through to Vite. The `/v1/`
    // prefix is load-bearing: Vite serves the app's OWN source under `/src/api/`,
    // so a bare `/api/` match would hijack the module graph and React never
    // mounts. Only `/api/v1/...` is the backend.
    if (!url.includes("/api/v1/")) return route.continue();

    // App-global poll → benign empty list (keeps the badge out of shots).
    if (url.includes("/api/v1/operation-runs")) {
      return fulfillJson(route, 200, {
        data: [],
        total: 0,
        limit: 50,
        offset: 0,
        next_cursor: null,
      });
    }

    // Most specific paths first.
    if (url.includes(`/links/${FIXTURE_LINK_ID}/sync/preview`)) {
      return apply(route, mocks.syncPreview);
    }
    if (url.includes(`/links/${FIXTURE_LINK_ID}/sync`)) {
      return apply(route, mocks.sync);
    }
    if (url.includes(`${base}/tracks`)) return apply(route, mocks.tracks);
    if (url.includes(`${base}/links`)) return apply(route, mocks.links);
    if (url.includes("/api/v1/connectors"))
      return apply(route, mocks.connectors);
    if (url.includes(base)) return apply(route, mocks.playlist);

    // Unmocked API → 404 (no retry, no churn).
    return fulfillJson(route, 404, {
      error: { code: "NOT_FOUND", message: "Unmocked endpoint" },
    });
  });
}
