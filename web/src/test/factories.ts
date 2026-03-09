/**
 * Shared test data factories for frontend component tests.
 *
 * Each factory returns a valid schema object with sensible defaults.
 * Pass an overrides object to customise individual fields.
 */

import type {
  PlaylistDetailSchema,
  PlaylistEntrySchema,
  PlaylistSummarySchema,
  WorkflowSummarySchema,
} from "@/api/generated/model";

export function makePlaylistSummary(
  overrides: Partial<PlaylistSummarySchema> = {},
): PlaylistSummarySchema {
  return {
    id: 1,
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
    id: 1,
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

export function makePlaylistEntry(
  overrides: Partial<{
    position: number;
    title: string;
    artist: string;
    album: string | null;
    duration_ms: number | null;
    added_at: string | null;
  }> = {},
): PlaylistEntrySchema {
  return {
    position: overrides.position ?? 1,
    track: {
      id: overrides.position ?? 1,
      title: overrides.title ?? "Test Track",
      artists: [{ name: overrides.artist ?? "Test Artist" }],
      album: overrides.album ?? null,
      duration_ms: overrides.duration_ms ?? null,
    },
    added_at: overrides.added_at ?? null,
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
      id: i + 100,
      title: item.title,
      artists: [{ name: item.artist }],
      album: item.album ?? null,
      duration_ms: item.duration_ms ?? null,
    },
    added_at: item.added_at ?? null,
  }));
}

export function makeWorkflowSummary(
  overrides: Partial<WorkflowSummarySchema> = {},
): WorkflowSummarySchema {
  return {
    id: 1,
    name: "Test Workflow",
    description: "A test workflow",
    is_template: false,
    source_template: null,
    definition_version: 1,
    task_count: 3,
    node_types: [
      "source.liked_tracks",
      "filter.play_count",
      "destination.playlist",
    ],
    created_at: "2026-01-10T08:00:00Z",
    updated_at: "2026-01-15T12:00:00Z",
    last_run: null,
    ...overrides,
  };
}
