/**
 * Fixture factories for the Playlist Detail visual audit.
 *
 * Each factory returns an {@link EndpointMocks} bundle — the responses the
 * route-mock installer serves for one captured state. Pure data, no Playwright
 * imports, so the Vitest sanity test can import these too.
 *
 * Types are imported type-only from the generated model (erased at runtime),
 * giving compile-time drift protection without a runtime alias dependency.
 */
import type {
  ConnectorLinkBriefSchema,
  ConnectorMetadataSchema,
  PaginatedResponsePlaylistEntrySchema,
  PlaylistDetailSchema,
  PlaylistEntrySchema,
  PlaylistLinkSchema,
  SyncPreviewResponse,
  TrackSummarySchema,
} from "../../src/api/generated/model";

export const FIXTURE_PLAYLIST_ID = "pl_fixture";
export const FIXTURE_LINK_ID = "lnk_fixture";

// ─── Mock-response model ─────────────────────────────────────────────────────

/** One endpoint's canned response. `pending` never resolves (skeleton capture). */
export type MockResponse =
  | { kind: "json"; status: number; body: unknown }
  | { kind: "error"; status: number; code: string; message: string }
  | { kind: "pending" };

export const json = (body: unknown, status = 200): MockResponse => ({
  kind: "json",
  status,
  body,
});
export const errorRes = (
  status: number,
  code = "ERROR",
  message = "Something went wrong",
): MockResponse => ({ kind: "error", status, code, message });
export const pending = (): MockResponse => ({ kind: "pending" });

/** The endpoints Playlist Detail (and its dialogs) read. Unset → 404 default. */
export interface EndpointMocks {
  playlist?: MockResponse;
  tracks?: MockResponse;
  links?: MockResponse;
  connectors?: MockResponse;
  syncPreview?: MockResponse;
  /** POST /sync result — only used by the dialog interaction scenarios. */
  sync?: MockResponse;
}

// ─── Atoms ───────────────────────────────────────────────────────────────────

const CATALOG: TrackSummarySchema[] = [
  {
    id: "trk_1",
    title: "Midnight City",
    artists: [{ name: "M83" }],
    album: "Hurry Up, We're Dreaming",
    duration_ms: 244_000,
  },
  {
    id: "trk_2",
    title: "Strobe",
    artists: [{ name: "deadmau5" }],
    album: "For Lack of a Better Name",
    duration_ms: 636_000,
  },
  {
    id: "trk_3",
    title: "Nightcall",
    artists: [{ name: "Kavinsky" }, { name: "Lovefoxxx" }],
    album: "OutRun",
    duration_ms: 258_000,
  },
  {
    id: "trk_4",
    title: "Resonance",
    artists: [{ name: "HOME" }],
    album: "Odyssey",
    duration_ms: 211_000,
  },
  {
    id: "trk_5",
    title: "Teardrop",
    artists: [{ name: "Massive Attack" }],
    album: "Mezzanine",
    duration_ms: 330_000,
  },
  {
    id: "trk_6",
    title: "An Ending (Ascent)",
    artists: [{ name: "Brian Eno" }],
    album: "Apollo",
    duration_ms: 252_000,
  },
  {
    id: "trk_7",
    title: "Silent Shout",
    artists: [{ name: "The Knife" }],
    album: "Silent Shout",
    duration_ms: 239_000,
  },
  {
    id: "trk_8",
    title: "Avril 14th",
    artists: [{ name: "Aphex Twin" }],
    album: "Drukqs",
    duration_ms: 125_000,
  },
];

export function makeTrack(
  overrides: Partial<TrackSummarySchema> = {},
): TrackSummarySchema {
  return { ...CATALOG[0], ...overrides };
}

/** A resolved entry pulled from the catalogue by index. */
export function makeEntry(
  position: number,
  trackIndex = position - 1,
): PlaylistEntrySchema {
  return {
    position,
    track: CATALOG[trackIndex % CATALOG.length],
    added_at: "2026-05-12T09:30:00Z",
    is_resolved: true,
  };
}

/** An unresolved entry — a source position with no canonical match (id=null). */
export function makeUnresolvedEntry(
  position: number,
  title: string,
): PlaylistEntrySchema {
  return {
    position,
    track: {
      id: null,
      title,
      artists: [{ name: "Unknown artist" }],
      album: null,
      duration_ms: null,
    },
    added_at: "2026-05-12T09:30:00Z",
    is_resolved: false,
  };
}

export function makeLink(
  overrides: Partial<PlaylistLinkSchema> = {},
): PlaylistLinkSchema {
  const direction = overrides.sync_direction ?? "pull";
  return {
    id: FIXTURE_LINK_ID,
    connector_name: "spotify",
    connector_playlist_identifier: "37i9dQZF1DXcBWIGoYBM5M",
    connector_playlist_name: "Roadtrip Mix",
    sync_direction: direction,
    direction_label:
      direction === "push"
        ? "Mixd → Spotify (replaces Spotify)"
        : "Spotify → Mixd (replaces Mixd)",
    sync_status: "never_synced",
    last_synced: null,
    last_sync_error: null,
    last_sync_tracks_added: null,
    last_sync_tracks_removed: null,
    last_sync_tracks_unmatched: null,
    ...overrides,
  };
}

function briefFromLink(link: PlaylistLinkSchema): ConnectorLinkBriefSchema {
  return {
    connector_name: link.connector_name,
    sync_direction: link.sync_direction,
    sync_status: link.sync_status,
  };
}

function makePlaylist(
  entries: PlaylistEntrySchema[],
  links: PlaylistLinkSchema[],
  overrides: Partial<PlaylistDetailSchema> = {},
): PlaylistDetailSchema {
  return {
    id: FIXTURE_PLAYLIST_ID,
    name: "Late Night Drive",
    description: "Synthwave and ambient for the long road home.",
    track_count: entries.length,
    connector_links: links.map(briefFromLink),
    updated_at: "2026-06-20T22:15:00Z",
    entries,
    ...overrides,
  };
}

function tracksPage(
  entries: PlaylistEntrySchema[],
): PaginatedResponsePlaylistEntrySchema {
  return {
    data: entries,
    total: entries.length,
    limit: 50,
    offset: 0,
    next_cursor: null,
  };
}

export function makeConnector(
  overrides: Partial<ConnectorMetadataSchema> = {},
): ConnectorMetadataSchema {
  return {
    name: "spotify",
    display_name: "Spotify",
    category: "streaming" as ConnectorMetadataSchema["category"],
    auth_method: "oauth" as ConnectorMetadataSchema["auth_method"],
    status: "connected" as ConnectorMetadataSchema["status"],
    connected: true,
    account_name: "ash",
    token_expires_at: null,
    capabilities: [],
    auth_error: null,
    last_synced_at: null,
    ...overrides,
  };
}

export function makePreview(
  overrides: Partial<SyncPreviewResponse> = {},
): SyncPreviewResponse {
  return {
    tracks_to_add: 0,
    tracks_to_remove: 0,
    tracks_unchanged: 0,
    direction: "pull",
    direction_label: "Spotify → Mixd (replaces Mixd)",
    connector_name: "spotify",
    playlist_name: "Roadtrip Mix",
    has_comparison_data: true,
    safety_flagged: false,
    confirm_token: "tok_fixture",
    ...overrides,
  };
}

// ─── Composite shorthands ────────────────────────────────────────────────────

const FULL_ENTRIES = Array.from({ length: 6 }, (_, i) => makeEntry(i + 1));

/** A standard populated playlist with one link in the given sync state. */
function populated(
  link: PlaylistLinkSchema,
  entries = FULL_ENTRIES,
): EndpointMocks {
  return {
    playlist: json(makePlaylist(entries, [link])),
    tracks: json(tracksPage(entries)),
    links: json([link]),
  };
}

// ─── Named state fixtures ────────────────────────────────────────────────────
// Keyed groups the audit spec iterates. Data only — interaction lives in the spec.

/** Page-level: the four-states rule + structural edge cases. */
export const pageStates = {
  loading: (): EndpointMocks => ({
    playlist: pending(),
    tracks: pending(),
    links: json([]),
  }),
  error: (): EndpointMocks => ({
    playlist: errorRes(404, "NOT_FOUND", "Playlist not found"),
  }),
  success: (): EndpointMocks =>
    populated(
      makeLink({ sync_status: "synced", last_synced: "2026-06-20T21:00:00Z" }),
    ),
  emptyPlaylist: (): EndpointMocks => ({
    playlist: json(makePlaylist([], [])),
    tracks: json(tracksPage([])),
    links: json([]),
  }),
  tracksLoading: (): EndpointMocks => ({
    playlist: json(makePlaylist(FULL_ENTRIES, [])),
    tracks: pending(),
    links: json([]),
  }),
  longName: (): EndpointMocks => ({
    ...populated(makeLink({ sync_status: "synced" })),
    playlist: json(
      makePlaylist(FULL_ENTRIES, [makeLink({ sync_status: "synced" })], {
        name: "Songs to Play While Watching the City Lights Blur Past at 2 A.M. — Vol. III",
        description:
          "An overlong, deliberately verbose description to stress-test wrapping, truncation, and the header's vertical rhythm across viewports and themes.",
      }),
    ),
  }),
};

/** Tracks section: unresolved entries drive the repair bar. */
export const trackStates = {
  withUnresolved: (): EndpointMocks => {
    const entries = [
      makeEntry(1),
      makeEntry(2),
      makeUnresolvedEntry(3, "Untitled Demo (rough mix)"),
      makeEntry(4),
      makeUnresolvedEntry(5, "Live at the Roundhouse 1979"),
    ];
    return {
      playlist: json(makePlaylist(entries, [])),
      tracks: json(tracksPage(entries)),
      links: json([]),
    };
  },
};

/** Linked Services: per-link sync_status × direction × results. */
export const linkStates = {
  none: (): EndpointMocks => ({
    playlist: json(makePlaylist(FULL_ENTRIES, [])),
    tracks: json(tracksPage(FULL_ENTRIES)),
    links: json([]),
  }),
  neverSyncedPull: (): EndpointMocks =>
    populated(
      makeLink({ sync_direction: "pull", sync_status: "never_synced" }),
    ),
  neverSyncedPush: (): EndpointMocks =>
    populated(
      makeLink({ sync_direction: "push", sync_status: "never_synced" }),
    ),
  synced: (): EndpointMocks =>
    populated(
      makeLink({
        sync_status: "synced",
        last_synced: "2026-06-20T21:00:00Z",
        last_sync_tracks_added: 4,
        last_sync_tracks_removed: 1,
      }),
    ),
  syncing: (): EndpointMocks => populated(makeLink({ sync_status: "syncing" })),
  error: (): EndpointMocks =>
    populated(
      makeLink({
        sync_status: "error",
        last_sync_error: "Spotify returned 502 while fetching the playlist.",
      }),
    ),
  withUnmatched: (): EndpointMocks =>
    populated(
      makeLink({
        sync_status: "synced",
        last_synced: "2026-06-20T21:00:00Z",
        last_sync_tracks_added: 12,
        last_sync_tracks_removed: 0,
        last_sync_tracks_unmatched: 3,
      }),
    ),
  multiple: (): EndpointMocks => {
    const links = [
      makeLink({
        id: "lnk_a",
        sync_direction: "pull",
        sync_status: "synced",
        last_synced: "2026-06-20T21:00:00Z",
        connector_playlist_name: "Roadtrip Mix",
      }),
      makeLink({
        id: "lnk_b",
        connector_name: "lastfm",
        sync_direction: "push",
        sync_status: "error",
        last_sync_error: "Auth token expired.",
        connector_playlist_name: "Loved Tracks",
      }),
    ];
    return {
      playlist: json(makePlaylist(FULL_ENTRIES, links)),
      tracks: json(tracksPage(FULL_ENTRIES)),
      links: json(links),
    };
  },
};

/**
 * Sync dialog fixtures. The row carries one link; `syncPreview` shapes the
 * dialog. The destructive view is driven entirely by `safety_flagged` in the
 * preview, so no POST is needed to capture it.
 */
export const syncDialogStates = {
  firstSync: (): EndpointMocks => ({
    ...populated(makeLink({ sync_status: "never_synced" })),
    syncPreview: json(makePreview({ has_comparison_data: false })),
  }),
  nonDestructive: (): EndpointMocks => ({
    ...populated(
      makeLink({ sync_status: "synced", last_synced: "2026-06-19T10:00:00Z" }),
    ),
    syncPreview: json(
      makePreview({
        tracks_to_add: 6,
        tracks_to_remove: 2,
        tracks_unchanged: 40,
      }),
    ),
  }),
  destructive: (): EndpointMocks => ({
    ...populated(
      makeLink({ sync_status: "synced", last_synced: "2026-06-19T10:00:00Z" }),
    ),
    syncPreview: json(
      makePreview({
        tracks_to_add: 0,
        tracks_to_remove: 47,
        tracks_unchanged: 3,
        safety_flagged: true,
        safety_removals: 47,
        safety_total: 50,
        safety_remaining: 3,
        safety_message:
          "This removes most of the playlist — double-check before continuing.",
      }),
    ),
  }),
  noop: (): EndpointMocks => ({
    ...populated(
      makeLink({ sync_status: "synced", last_synced: "2026-06-19T10:00:00Z" }),
    ),
    syncPreview: json(
      makePreview({
        tracks_to_add: 0,
        tracks_to_remove: 0,
        tracks_unchanged: 50,
      }),
    ),
  }),
  previewLoading: (): EndpointMocks => ({
    ...populated(makeLink({ sync_status: "synced" })),
    syncPreview: pending(),
  }),
  previewError: (): EndpointMocks => ({
    ...populated(makeLink({ sync_status: "synced" })),
    syncPreview: errorRes(500, "PREVIEW_FAILED", "Preview failed"),
  }),
};

/** Edit / Delete / Link dialogs ride on a populated, connector-aware page. */
export const dialogStates = {
  base: (): EndpointMocks => ({
    ...populated(
      makeLink({ sync_status: "synced", last_synced: "2026-06-20T21:00:00Z" }),
    ),
    connectors: json([
      makeConnector(),
      makeConnector({ name: "lastfm", display_name: "Last.fm" }),
    ]),
  }),
};
