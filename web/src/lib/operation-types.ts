/**
 * Single source of truth for the operation types that produce an OperationRun
 * audit row (matches the v0.7.7 backend vocabulary).
 *
 * One entry per type carries everything the UI needs: the display label for run
 * logs and the terminal-toast spec. The table is keyed on the full union, so a
 * new backend operation type is a compile error until every surface has copy
 * for it.
 */

import { pluralize } from "./pluralize";

export type RunOperationType =
  | "import_lastfm_history"
  | "import_spotify_likes"
  | "export_lastfm_likes"
  | "import_spotify_history"
  | "import_spotify_recent"
  | "import_apple_recent"
  | "import_connector_playlists"
  | "apply_assignments_bulk";

export interface OperationTypeSpec {
  /** Display name for run history rows and logs. */
  label: string;
  /**
   * REAL backend `summary_metrics` names emitted in a terminal event's `counts`
   * (from `OperationResult.to_counts()`). The first key that resolves to a
   * number feeds the title; none resolving means 0 → the bare "… complete"
   * phrasing.
   */
  countKeys: readonly string[];
  /** Terminal-toast title for the resolved count. */
  title: (count: number) => string;
}

export const OPERATION_TYPES: Record<RunOperationType, OperationTypeSpec> = {
  import_lastfm_history: {
    label: "Last.fm history import",
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0 ? `Imported ${pluralize(n, "scrobble")}` : "Import complete",
  },
  import_spotify_likes: {
    label: "Spotify likes import",
    countKeys: ["imported", "already_liked", "candidates"],
    title: (n) =>
      n > 0 ? `Imported ${pluralize(n, "like")}` : "Import complete",
  },
  export_lastfm_likes: {
    label: "Last.fm likes export",
    countKeys: ["exported", "already_loved", "candidates"],
    title: (n) =>
      n > 0 ? `Exported ${pluralize(n, "love")}` : "Export complete",
  },
  import_spotify_history: {
    label: "Spotify history import",
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0 ? `Imported ${pluralize(n, "scrobble")}` : "Import complete",
  },
  import_spotify_recent: {
    label: "Spotify recent plays import",
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0
        ? `Imported ${n} recent ${pluralize(n, "play")}`
        : "Already up to date",
  },
  import_apple_recent: {
    label: "Apple Music recent plays import",
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0
        ? `Imported ${n} recent ${pluralize(n, "play")}`
        : "Already up to date",
  },
  import_connector_playlists: {
    label: "Playlist import",
    countKeys: ["succeeded", "imported"],
    title: (n) =>
      n > 0 ? `Imported ${pluralize(n, "playlist")}` : "Import complete",
  },
  apply_assignments_bulk: {
    label: "Apply all assignments",
    countKeys: ["updated", "assignments_processed"],
    title: (n) =>
      n > 0 ? `Applied ${pluralize(n, "assignment")}` : "Apply complete",
  },
};

/** Whether a wire-level `operation_type` string is one this build knows. */
export function isRunOperationType(value: string): value is RunOperationType {
  return Object.hasOwn(OPERATION_TYPES, value);
}

/**
 * Display label for a wire-level `operation_type`.
 *
 * `operation_type` is an unconstrained string on the API schema, so a run
 * written by a newer backend renders as a generic phrase rather than an
 * unreadable identifier.
 */
export function operationLabel(operationType: string): string {
  return isRunOperationType(operationType)
    ? OPERATION_TYPES[operationType].label
    : "Operation";
}
