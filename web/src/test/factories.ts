/**
 * Shared test data factories for frontend component tests.
 *
 * Each factory returns a valid schema object with sensible defaults.
 * Pass an overrides object to customise individual fields.
 */

import type {
  ConnectorMetadataSchema,
  ConnectorPlaylistBrowseSchema,
  PlaylistDetailSchema,
  PlaylistEntrySchema,
  PlaylistSummarySchema,
} from "#/api/generated/model";
import { connectorBrand } from "#/lib/connector-brand";

export function makeConnectorPlaylistBrowse(
  overrides: Partial<ConnectorPlaylistBrowseSchema> = {},
): ConnectorPlaylistBrowseSchema {
  const suffix = overrides.connector_playlist_identifier ?? "sp1";
  return {
    connector_playlist_identifier: suffix,
    connector_playlist_db_id: `019da92c-0000-74aa-9a86-0000000000${suffix.replace(/\D/g, "").padStart(2, "0").slice(-2)}`,
    name: "Test Playlist",
    description: null,
    owner: "me",
    image_url: null,
    track_count: 100,
    snapshot_id: `snap-${suffix}`,
    collaborative: false,
    is_public: true,
    import_status: "not_imported",
    current_assignments: [],
    ...overrides,
  };
}

/** Capability / category / auth-method defaults tuned per connector so tests
 *  don't have to hand-code common shapes. ``display_name`` is sourced from
 *  ``connectorBrand`` so tests share one source of truth with the UI. */
const connectorDefaults: Record<
  string,
  Pick<ConnectorMetadataSchema, "category" | "auth_method" | "capabilities">
> = {
  spotify: {
    category: "streaming",
    auth_method: "oauth",
    capabilities: [
      "history_import_file",
      "likes_import",
      "playlist_import",
      "playlist_sync",
      "track_enrichment",
    ],
  },
  lastfm: {
    category: "history",
    auth_method: "oauth",
    capabilities: ["history_import_api", "love_tracks", "track_enrichment"],
  },
  musicbrainz: {
    category: "enrichment",
    auth_method: "none",
    capabilities: ["track_enrichment"],
  },
  apple_music: {
    category: "streaming",
    auth_method: "coming_soon",
    capabilities: [],
  },
};

export function makeConnectorMetadata(
  overrides: Partial<ConnectorMetadataSchema> & { name: string },
): ConnectorMetadataSchema {
  const d = connectorDefaults[overrides.name] ?? {
    category: "enrichment" as const,
    auth_method: "none" as const,
    capabilities: [],
  };
  const display_name = connectorBrand[overrides.name]?.label ?? overrides.name;
  const connected = overrides.connected ?? false;
  // Mirrors derive_status_state() in the backend so tests that omit
  // ``status`` still pass through the right RowAction branch. ``auth_error``
  // wins over the static auth_method lookup (matching backend ordering).
  const defaultStatus: ConnectorMetadataSchema["status"] = overrides.auth_error
    ? "error"
    : ({
        coming_soon: "coming_soon",
        none: "public_api",
        oauth: connected ? "connected" : "disconnected",
      }[d.auth_method] as ConnectorMetadataSchema["status"]);
  return {
    display_name,
    category: d.category,
    auth_method: d.auth_method,
    capabilities: d.capabilities,
    status: defaultStatus,
    connected,
    account_name: null,
    token_expires_at: null,
    ...overrides,
  };
}

export function makePlaylistSummary(
  overrides: Partial<PlaylistSummarySchema> = {},
): PlaylistSummarySchema {
  return {
    id: "019d0000-0000-7000-8000-000000000001",
    name: "Test Playlist",
    description: "A test description",
    track_count: 10,
    connector_links: [
      {
        connector_name: "spotify",
        sync_direction: "push",
        sync_status: "synced",
      },
    ],
    updated_at: "2026-01-15T12:00:00Z",
    ...overrides,
  };
}

export function makePlaylistDetail(
  overrides: Partial<PlaylistDetailSchema> = {},
): PlaylistDetailSchema {
  return {
    id: "019d0000-0000-7000-8000-000000000002",
    name: "Test Playlist",
    description: "A test description",
    track_count: 3,
    connector_links: [
      {
        connector_name: "spotify",
        sync_direction: "push",
        sync_status: "synced",
      },
    ],
    updated_at: "2026-01-15T12:00:00Z",
    entries: [],
    ...overrides,
  };
}

export function makePlaylistEntries(
  items: Array<{
    title: string;
    artist: string;
    album?: string | null;
    duration_ms?: number | null;
    added_at?: string | null;
  }>,
): PlaylistEntrySchema[] {
  return items.map((item, i) => ({
    position: i + 1,
    track: {
      id: `019d0000-0000-7000-8000-${String(i + 100).padStart(12, "0")}`,
      title: item.title,
      artists: [{ name: item.artist }],
      album: item.album ?? null,
      duration_ms: item.duration_ms ?? null,
    },
    added_at: item.added_at ?? null,
  }));
}
