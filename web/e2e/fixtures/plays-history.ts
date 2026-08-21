/**
 * Fixtures + route installer for the v0.10.4 play-history audit harness:
 * `/library/plays`, the Library play columns/filters, and Track Detail's
 * Play History section. Typed against the generated models so schema drift
 * fails `tsc` (pulled into the type program by
 * `src/test/plays-history-fixtures.test.ts`).
 */
import type { Page, Route } from "@playwright/test";

import type { ConnectorMetadataSchema } from "../../src/api/generated/model/connectorMetadataSchema";
import type { LibraryTrackSchema } from "../../src/api/generated/model/libraryTrackSchema";
import type { PaginatedLibraryTracksResponse } from "../../src/api/generated/model/paginatedLibraryTracksResponse";
import type { PlayEventSchema } from "../../src/api/generated/model/playEventSchema";
import type { PlayHistogramResponse } from "../../src/api/generated/model/playHistogramResponse";
import type { PlayListResponse } from "../../src/api/generated/model/playListResponse";
import type { TrackDetailSchema } from "../../src/api/generated/model/trackDetailSchema";

export const FIXTURE_TRACK_ID = "01890a5d-ac96-774b-bcce-b302099a8057";

/** The clock the audit spec freezes before goto — timestamps below hang off it. */
export const FIXED_NOW = new Date("2026-08-20T12:00:00Z");

const hoursAgo = (h: number) =>
  new Date(FIXED_NOW.getTime() - h * 3_600_000).toISOString();
const daysAgo = (d: number, h = 0) =>
  new Date(FIXED_NOW.getTime() - (d * 24 + h) * 3_600_000).toISOString();

export type MockResponse =
  | { kind: "pending" }
  | { kind: "error"; status: number; code: string; message: string }
  | { kind: "json"; status: number; body: unknown };

const json = (body: unknown, status = 200): MockResponse => ({
  kind: "json",
  status,
  body,
});

export interface EndpointMocks {
  plays?: MockResponse;
  histogram?: MockResponse;
  tracks?: MockResponse;
  trackDetail?: MockResponse;
  connectors?: MockResponse;
}

// ─── Play events ─────────────────────────────────────────────────────────────

let seq = 0;
function makePlay(overrides: Partial<PlayEventSchema>): PlayEventSchema {
  seq += 1;
  return {
    id: `01890a5d-ac96-774b-bcce-b30209${String(seq).padStart(6, "0")}`,
    track_id: FIXTURE_TRACK_ID,
    title: "Polygon Window",
    artists: "Aphex Twin",
    played_at: hoursAgo(3),
    ms_played: 214_000,
    service: "spotify",
    source_services: ["spotify"],
    ...overrides,
  };
}

/** Multi-day feed: a collapse run today, mixed sources, a long-title row. */
const FEED: PlayEventSchema[] = [
  // Today — three consecutive plays of one track (collapses to ×3)…
  makePlay({ played_at: hoursAgo(2) }),
  makePlay({ played_at: hoursAgo(2.2) }),
  makePlay({ played_at: hoursAgo(2.4) }),
  // …then two other tracks, one with a very long title + merged sources.
  makePlay({
    track_id: "01890a5d-ac96-774b-bcce-b30209000101",
    title:
      "Everything Is Recorded feat. Sampha & Owen Pallett (Extended Symphonic Rework)",
    artists: "Everything Is Recorded, Sampha, Owen Pallett",
    played_at: hoursAgo(5),
    source_services: ["spotify", "lastfm"],
  }),
  makePlay({
    track_id: "01890a5d-ac96-774b-bcce-b30209000102",
    title: "Kobresia",
    artists: "Biosphere",
    played_at: hoursAgo(9),
    service: "lastfm",
    source_services: ["lastfm"],
    ms_played: null,
  }),
  // Yesterday.
  makePlay({
    track_id: "01890a5d-ac96-774b-bcce-b30209000103",
    title: "Rausch",
    artists: "GAS",
    played_at: daysAgo(1, 2),
  }),
  makePlay({ played_at: daysAgo(1, 4) }),
  makePlay({
    track_id: "01890a5d-ac96-774b-bcce-b30209000102",
    title: "Kobresia",
    artists: "Biosphere",
    played_at: daysAgo(1, 8),
    service: "lastfm",
    source_services: ["lastfm"],
    ms_played: null,
  }),
  // An older absolute-labeled day.
  makePlay({ played_at: daysAgo(10, 1) }),
  makePlay({
    track_id: "01890a5d-ac96-774b-bcce-b30209000103",
    title: "Rausch",
    artists: "GAS",
    played_at: daysAgo(10, 3),
    source_services: ["spotify", "lastfm"],
  }),
];

const HISTOGRAM: PlayHistogramResponse = {
  bucket: "week",
  bins: Array.from({ length: 12 }, (_, i) => ({
    bucket_start: daysAgo((11 - i) * 7 + 3),
    count: [4, 9, 2, 0, 7, 14, 6, 3, 11, 5, 8, 10][i],
  })),
};

// ─── Library rows ────────────────────────────────────────────────────────────

function makeLibraryTrack(
  overrides: Partial<LibraryTrackSchema> & { id: string },
): LibraryTrackSchema {
  return {
    title: "Polygon Window",
    artists: [{ name: "Aphex Twin" }],
    album: "Surfing on Sine Waves",
    duration_ms: 214_000,
    isrc: null,
    connector_names: ["spotify"],
    is_liked: false,
    tags: [],
    total_plays: 0,
    last_played: null,
    ...overrides,
  };
}

const LIBRARY_ROWS: LibraryTrackSchema[] = [
  makeLibraryTrack({
    id: FIXTURE_TRACK_ID,
    total_plays: 143,
    last_played: hoursAgo(3),
    is_liked: true,
    preference: "star",
    tags: ["mood:hypnotic", "energy:low"],
  }),
  makeLibraryTrack({
    id: "01890a5d-ac96-774b-bcce-b30209000101",
    title:
      "Everything Is Recorded feat. Sampha & Owen Pallett (Extended Symphonic Rework)",
    artists: [{ name: "Everything Is Recorded" }, { name: "Sampha" }],
    album: "Friday Forever (A Very Long Album Title Edition)",
    total_plays: 1204,
    last_played: hoursAgo(5),
    connector_names: ["spotify", "lastfm"],
  }),
  makeLibraryTrack({
    id: "01890a5d-ac96-774b-bcce-b30209000102",
    title: "Kobresia",
    artists: [{ name: "Biosphere" }],
    album: "Substrata",
    total_plays: 47,
    last_played: daysAgo(2),
    connector_names: ["lastfm"],
    preference: "yah",
  }),
  makeLibraryTrack({
    id: "01890a5d-ac96-774b-bcce-b30209000103",
    title: "Rausch",
    artists: [{ name: "GAS" }],
    album: "Rausch",
    total_plays: 8,
    last_played: daysAgo(230),
    tags: ["mood:dense"],
  }),
  makeLibraryTrack({
    id: "01890a5d-ac96-774b-bcce-b30209000104",
    title: "Untitled #3",
    artists: [{ name: "Sigur Rós" }],
    album: "( )",
    total_plays: 1,
    last_played: daysAgo(800),
  }),
  makeLibraryTrack({
    id: "01890a5d-ac96-774b-bcce-b30209000105",
    title: "Never Played Demo",
    artists: [{ name: "Unknown Artist" }],
    album: null,
    duration_ms: null,
  }),
];

const CONNECTORS: ConnectorMetadataSchema[] = [
  {
    name: "spotify",
    display_name: "Spotify",
    category: "streaming",
    auth_method: "oauth",
    status: "connected",
    connected: true,
    capabilities: [],
  },
  {
    name: "lastfm",
    display_name: "Last.fm",
    category: "history",
    auth_method: "oauth",
    status: "connected",
    connected: true,
    capabilities: [],
  },
];

// ─── Track detail ────────────────────────────────────────────────────────────

function makeTrackDetail(totalPlays: number): TrackDetailSchema {
  return {
    id: FIXTURE_TRACK_ID,
    title: "Polygon Window",
    artists: [{ name: "Aphex Twin" }],
    album: "Surfing on Sine Waves",
    duration_ms: 214_000,
    isrc: "GBAAA9300003",
    connector_mappings: [
      {
        mapping_id: "01890a5d-ac96-774b-bcce-b30209000201",
        connector_name: "spotify",
        connector_track_id: "5FGH2mkETSNQXlIte1Pu2b",
        match_method: "direct",
        confidence: 100,
        origin: "import",
        is_primary: true,
        connector_track_title: "Polygon Window",
        connector_track_artists: ["Aphex Twin"],
      },
    ],
    like_status: {
      spotify: { is_liked: true, liked_at: daysAgo(400) },
    },
    play_summary: {
      total_plays: totalPlays,
      first_played: daysAgo(1200),
      last_played: hoursAgo(3),
    },
    playlists: [
      {
        id: "01890a5d-ac96-774b-bcce-b30209000301",
        name: "Ambient Staples",
        description: "Slow rotation, heavy gravity.",
      },
    ],
    preference: "star",
    tags: ["mood:hypnotic", "energy:low"],
  };
}

// ─── Scenario states ─────────────────────────────────────────────────────────

const emptyPlays: PlayListResponse = {
  data: [],
  limit: 100,
  next_cursor: null,
};

export const playsStates = {
  populated: (): EndpointMocks => ({
    plays: json({ data: FEED, limit: 100, next_cursor: null }),
    histogram: json(HISTOGRAM),
    connectors: json(CONNECTORS),
  }),
  empty: (): EndpointMocks => ({
    plays: json(emptyPlays),
    histogram: json({ bucket: "month", bins: [] }),
    connectors: json(CONNECTORS),
  }),
  loading: (): EndpointMocks => ({
    plays: { kind: "pending" },
    histogram: { kind: "pending" },
    connectors: json(CONNECTORS),
  }),
};

export const libraryStates = {
  populated: (): EndpointMocks => ({
    tracks: json({
      data: LIBRARY_ROWS,
      total: LIBRARY_ROWS.length,
      limit: 50,
      offset: 0,
      next_cursor: null,
      facets: null,
    } satisfies PaginatedLibraryTracksResponse),
    connectors: json(CONNECTORS),
  }),
};

export const trackDetailStates = {
  manyPlays: (): EndpointMocks => ({
    trackDetail: json(makeTrackDetail(143)),
    plays: json({ data: FEED.slice(0, 6), limit: 10, next_cursor: null }),
    histogram: json(HISTOGRAM),
    connectors: json(CONNECTORS),
  }),
  fewPlays: (): EndpointMocks => ({
    trackDetail: json(makeTrackDetail(3)),
    plays: json({ data: FEED.slice(0, 3), limit: 10, next_cursor: null }),
    // Below the chart threshold the histogram query is disabled; fixture
    // present anyway so an accidental fetch renders rather than 404s.
    histogram: json(HISTOGRAM),
    connectors: json(CONNECTORS),
  }),
};

// ─── Route installer ─────────────────────────────────────────────────────────

function fulfillJson(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function apply(route: Route, res: MockResponse | undefined): Promise<void> {
  if (!res) {
    return fulfillJson(route, 404, {
      error: { code: "NOT_FOUND", message: "No fixture for this endpoint" },
    });
  }
  switch (res.kind) {
    case "pending":
      return new Promise<void>(() => {});
    case "error":
      return fulfillJson(route, res.status, {
        error: { code: res.code, message: res.message },
      });
    case "json":
      return fulfillJson(route, res.status, res.body);
  }
}

export async function installPlaysAuditRoutes(
  page: Page,
  mocks: EndpointMocks = {},
): Promise<void> {
  await page.route("**/*", (route) => {
    const url = route.request().url();

    // `/api/v1/` prefix is load-bearing — Vite serves app source under /src/api/.
    if (!url.includes("/api/v1/")) return route.continue();

    if (url.includes("/api/v1/operation-runs")) {
      return fulfillJson(route, 200, {
        data: [],
        total: 0,
        limit: 50,
        offset: 0,
        next_cursor: null,
      });
    }

    // Most specific first.
    if (url.includes("/api/v1/plays/histogram")) {
      return apply(route, mocks.histogram);
    }
    if (url.includes("/api/v1/plays")) return apply(route, mocks.plays);
    if (url.includes(`/api/v1/tracks/${FIXTURE_TRACK_ID}`)) {
      return apply(route, mocks.trackDetail);
    }
    if (url.includes("/api/v1/tracks")) return apply(route, mocks.tracks);
    if (url.includes("/api/v1/connectors")) {
      return apply(route, mocks.connectors);
    }

    return fulfillJson(route, 404, {
      error: { code: "NOT_FOUND", message: "Unmocked endpoint" },
    });
  });
}
