/**
 * Pure serializer: Library filter state → WorkflowDef.
 *
 * Powers the Quick Filter UI's "Save as Workflow" action. The produced DAG
 * mirrors the v0.7.5 templates in src/application/workflows/definitions/ so
 * a saved filter round-trips cleanly through the workflow editor.
 *
 * Source-node selection (the tricky part):
 * - preference set                 → source.preferred_tracks(state=<pref>)
 * - liked=true                     → source.liked_tracks
 * - otherwise                      → source.liked_tracks (scope narrowing —
 *                                    the Library page itself shows all tracks,
 *                                    but the workflow DAG has no "all tracks"
 *                                    source node. Surfaced in the dialog.)
 */

import type { WorkflowDefSchemaInput } from "#/api/generated/model/workflowDefSchemaInput";
import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model/workflowTaskDefSchemaInput";
import type { PreferenceState } from "#/components/shared/PreferenceToggle";

export type TagMatchMode = "and" | "or";

/** Runtime list of valid preference states — used for URL param validation. */
export const PREFERENCE_STATES: readonly PreferenceState[] = [
  "hmm",
  "nah",
  "yah",
  "star",
] as const;

/** Narrow an unknown string to PreferenceState; returns null for invalid/null. */
export function parsePreferenceParam(
  raw: string | null,
): PreferenceState | null {
  return raw !== null && (PREFERENCE_STATES as readonly string[]).includes(raw)
    ? (raw as PreferenceState)
    : null;
}

export interface LibraryFilterState {
  /** Single preference state filter (multi-select is deferred; API is single-value). */
  preference?: PreferenceState | null;
  /** Tag values (already normalized by the UI). */
  tags?: string[];
  /** AND → match all tags, OR → match any. Defaults to AND. */
  tagMode?: TagMatchMode;
  /** Whether to restrict to liked tracks. `null`/undefined means "don't constrain". */
  liked?: boolean | null;
  /** Optional connector scope (e.g., "spotify"). */
  connector?: string | null;
  /** Maximum tracks to include in the resulting playlist. */
  limit?: number;
}

export interface SerializerMeta {
  /** User-provided workflow name. */
  name: string;
  /** Optional description — defaults to a computed summary. */
  description?: string;
  /** Destination playlist name template. Defaults to `{name} {date}`. */
  playlistName?: string;
}

const DEFAULT_LIMIT = 100;
const DEFAULT_CONNECTOR = "spotify";

/** Does the filter state carry any meaningful signal? Used to gate the Save button. */
export function hasActiveFilters(state: LibraryFilterState): boolean {
  return Boolean(
    state.preference ||
      (state.tags && state.tags.length > 0) ||
      state.liked === true ||
      state.liked === false ||
      state.connector,
  );
}

/**
 * Convert filter state + metadata into a WorkflowDef the API will accept.
 *
 * The source-node choice is deterministic:
 * - If `preference` is set, we can go straight to `source.preferred_tracks` and
 *   skip the enricher+filter.by_preference round-trip. Cheaper and clearer.
 * - If `liked` is true (or no preference is set), we fall back to
 *   `source.liked_tracks`.
 *
 * The slug is derived from the name — only lowercase alnum + underscores, so
 * it round-trips cleanly through JSON storage and URL paths.
 */
export function filtersToWorkflowDef(
  state: LibraryFilterState,
  meta: SerializerMeta,
): WorkflowDefSchemaInput {
  const id = toWorkflowId(meta.name);
  const tasks: WorkflowTaskDefSchemaInput[] = [];
  const limit = state.limit ?? DEFAULT_LIMIT;

  // === Source ===
  let lastTaskId: string;
  if (state.preference) {
    tasks.push({
      id: "source",
      type: "source.preferred_tracks",
      config: { state: state.preference, limit },
    });
    lastTaskId = "source";
  } else {
    tasks.push({
      id: "source",
      type: "source.liked_tracks",
      config: state.connector ? { connector_filter: state.connector } : {},
    });
    lastTaskId = "source";
  }

  // === Tag filter (requires enricher.tags upstream) ===
  if (state.tags && state.tags.length > 0) {
    tasks.push({
      id: "enrich_tags",
      type: "enricher.tags",
      config: {},
      upstream: [lastTaskId],
    });
    tasks.push({
      id: "filter_tags",
      type: "filter.by_tag",
      config: {
        tags: state.tags,
        match_mode: state.tagMode === "or" ? "any" : "all",
      },
      upstream: ["enrich_tags"],
    });
    lastTaskId = "filter_tags";
  }

  // === Connector filter (only meaningful when preference was the source;
  // liked-tracks path already carries connector_filter on the source config) ===
  // Deliberately skipped: no filter.by_connector node exists. Would require a
  // backend extension. Surfaced in the dialog note if state.connector is set
  // alongside a preference.

  // === Selector: always cap at limit ===
  tasks.push({
    id: "limit",
    type: "selector.limit_tracks",
    config: { count: limit, method: "first" },
    upstream: [lastTaskId],
  });

  // === Destination ===
  const playlistName = meta.playlistName ?? `${meta.name} {date}`;
  tasks.push({
    id: "create_playlist",
    type: "destination.create_playlist",
    config: {
      name: playlistName,
      description:
        meta.description ?? summarizeFilters(state, "({track_count} tracks)"),
      connector: DEFAULT_CONNECTOR,
    },
    upstream: ["limit"],
  });

  return {
    id,
    name: meta.name,
    description: meta.description ?? summarizeFilters(state),
    version: "1.0",
    tasks,
  };
}

/** Human-readable one-liner describing the active filter state. */
export function summarizeFilters(
  state: LibraryFilterState,
  suffix?: string,
): string {
  const parts: string[] = [];
  if (state.preference) parts.push(`preference=${state.preference}`);
  if (state.tags && state.tags.length > 0) {
    const joiner = state.tagMode === "or" ? " OR " : " AND ";
    parts.push(`tags=${state.tags.join(joiner)}`);
  }
  if (state.liked === true) parts.push("liked");
  else if (state.liked === false) parts.push("not liked");
  if (state.connector) parts.push(`connector=${state.connector}`);

  const base =
    parts.length > 0 ? `Saved filter: ${parts.join(", ")}` : "Saved filter";
  return suffix ? `${base} ${suffix}` : base;
}

/** Derive a lowercase, alnum-underscore workflow ID from a display name. */
export function toWorkflowId(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug || "saved_filter";
}
