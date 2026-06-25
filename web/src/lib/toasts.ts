/**
 * Composable toast layer.
 *
 * Components call `toasts.success` / `toasts.error` / `toasts.promise` for
 * explicit notifications. Mutations should prefer declaring
 * `meta: { errorLabel }` on the mutation — the global `MutationCache.onError`
 * handler formats and shows the toast automatically.
 *
 * `formatApiError` maps known `ApiError.code` values to friendly titles so
 * infrastructure errors (rate limit, database unavailable, …) render
 * consistently everywhere they surface.
 */

import type { Mutation } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { ApiError } from "#/api/client";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  description?: ReactNode;
  id?: string | number;
  action?: ToastAction;
  duration?: number;
}

/** Human-readable `{ title, description }` for any thrown error. */
export function formatApiError(err: unknown): {
  title: string;
  description?: string;
} {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "DATABASE_UNAVAILABLE":
        return {
          title: "Database unavailable",
          description: "We'll retry automatically. Your changes aren't lost.",
        };
      case "CONNECTOR_NOT_AVAILABLE":
        return {
          title: "Service unavailable",
          description: err.message,
        };
      case "CONNECTOR_NOT_CONNECTED":
        return {
          title: "Not connected",
          description: `${err.message} Connect it in Integrations first.`,
        };
      case "LASTFM_AUTH_REQUIRED":
      case "SPOTIFY_AUTH_REQUIRED":
        return { title: "Not connected", description: err.message };
      case "VALIDATION_ERROR":
      case "VALIDATION_FAILED":
        return { title: "Invalid input", description: err.message };
      case "NOT_FOUND":
        return { title: "Not found", description: err.message };
      case "UNAUTHORIZED":
        return { title: "Signed out", description: "Please sign in again." };
      case "FORBIDDEN":
        return {
          title: "Not allowed",
          description: err.message,
        };
      case "RATE_LIMITED":
        return {
          title: "Too many requests",
          description: "Please try again in a moment.",
        };
      default:
        return { title: err.message };
    }
  }
  if (err instanceof Error) return { title: err.message };
  return { title: "Something went wrong" };
}

/** Operation types that produce an OperationRun audit row (matches v0.7.7 backend). */
export type RunOperationType =
  | "import_lastfm_history"
  | "import_spotify_likes"
  | "export_lastfm_likes"
  | "import_spotify_history"
  | "import_connector_playlists"
  | "apply_assignments_bulk";

/**
 * Per-run-type terminal-toast spec: the count keys that feed the title and the
 * title itself. `countKeys` are the REAL backend `summary_metrics` names emitted
 * in the terminal event's `counts` (from `OperationResult.to_counts()`); the
 * first that resolves to a number wins, else 0 → the bare "… complete" title.
 */
const RUN_TYPES: Record<
  RunOperationType,
  { countKeys: readonly string[]; title: (count: number) => string }
> = {
  import_lastfm_history: {
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0
        ? `Imported ${n} ${n === 1 ? "scrobble" : "scrobbles"}`
        : "Import complete",
  },
  import_spotify_likes: {
    countKeys: ["imported", "already_liked", "candidates"],
    title: (n) =>
      n > 0 ? `Imported ${n} ${n === 1 ? "like" : "likes"}` : "Import complete",
  },
  export_lastfm_likes: {
    countKeys: ["exported", "already_loved", "candidates"],
    title: (n) =>
      n > 0 ? `Exported ${n} ${n === 1 ? "love" : "loves"}` : "Export complete",
  },
  import_spotify_history: {
    countKeys: ["track_plays", "connector_plays", "raw_plays"],
    title: (n) =>
      n > 0
        ? `Imported ${n} ${n === 1 ? "scrobble" : "scrobbles"}`
        : "Import complete",
  },
  import_connector_playlists: {
    countKeys: ["succeeded", "imported"],
    title: (n) =>
      n > 0
        ? `Imported ${n} ${n === 1 ? "playlist" : "playlists"}`
        : "Import complete",
  },
  apply_assignments_bulk: {
    countKeys: ["updated", "assignments_processed"],
    title: (n) =>
      n > 0
        ? `Applied ${n} ${n === 1 ? "assignment" : "assignments"}`
        : "Apply complete",
  },
};

/** Run-terminal toast title from its counts, with a generic fallback for
 *  operation types that have no dedicated spec (so announce-all never crashes
 *  on an unknown type). */
function runTitle(
  operationType: string,
  counts: Record<string, unknown>,
  failed: boolean,
): string {
  const spec = RUN_TYPES[operationType as RunOperationType];
  if (!spec) return failed ? "Operation failed" : "Operation complete";
  for (const key of spec.countKeys) {
    const v = counts[key];
    if (typeof v === "number") return spec.title(v);
  }
  return spec.title(0);
}

export const toasts = {
  success(title: string, options: ToastOptions = {}) {
    toast.success(title, options);
  },

  /** Formats an arbitrary error into description; shows under `title`. */
  error(title: string, error: unknown, options: ToastOptions = {}) {
    const { description } = formatApiError(error);
    toast.error(title, { description, ...options });
  },

  /** Plain error without a thrown exception (UX messages, validation, etc.). */
  message(title: string, options: ToastOptions = {}) {
    toast.error(title, options);
  },

  info(title: string, options: ToastOptions = {}) {
    toast.info(title, options);
  },

  /**
   * Sonner's promise toast — loading → success / error in one call.
   * Use for long-running operations where the pending state matters
   * (syncs, imports, multi-step flows).
   */
  promise<T>(
    promise: Promise<T>,
    messages: {
      loading: string;
      success: string | ((result: T) => string);
      error?: string | ((err: unknown) => string);
    },
  ) {
    return toast.promise(promise, {
      loading: messages.loading,
      success: messages.success,
      error: messages.error ?? ((err) => formatApiError(err).title),
    });
  },

  /**
   * Post-run completion toast for any v0.7.7 OperationRun-backed flow.
   *
   * Variant rules:
   * - 0 issues → success toast, no action
   * - ≥1 issues → warning toast with "View log" action that navigates
   *   to /settings/imports?run=<runId>
   * - error → error toast with the same action
   *
   * The default "View log" action is omitted when ``runId`` is null (e.g., the
   * audit row couldn't be persisted) so the toast doesn't deep-link nowhere.
   * Pass ``action`` to override it (e.g. "Retry failed only").
   *
   * ``operationType`` is a plain string: known types get a tailored title from
   * {@link RUN_TITLES}; anything else falls back to a generic phrase, so the
   * global operations watcher can announce any run.
   */
  runCompleted({
    operationType,
    counts,
    issueCount,
    runId,
    failed = false,
    onNavigate,
    action: actionOverride,
  }: {
    operationType: string;
    counts: Record<string, unknown>;
    issueCount: number;
    runId: string | null;
    failed?: boolean;
    onNavigate: (path: string) => void;
    /** Replaces the default "View log" action when provided. */
    action?: ToastAction;
  }) {
    const title = runTitle(operationType, counts, failed);
    const description =
      issueCount > 0
        ? `${issueCount} ${issueCount === 1 ? "item" : "items"} had issues`
        : undefined;
    const defaultAction: ToastAction | undefined =
      runId !== null && (issueCount > 0 || failed)
        ? {
            label: "View log",
            onClick: () => onNavigate(`/settings/imports?run=${runId}`),
          }
        : undefined;
    const action = actionOverride ?? defaultAction;

    if (failed) {
      toast.error(title, { description, action });
    } else if (issueCount > 0) {
      toast.warning(title, { description, action });
    } else {
      toast.success(title, { description });
    }
  },
};

/**
 * `MutationCache.onError` handler.
 *
 * Reads two optional `meta` fields on the mutation:
 *   - `errorLabel`: string — the toast title. Falls back to a generic message.
 *   - `suppressErrorToast`: true — skip the global toast when the caller
 *     already shows an inline error (e.g., form-level field errors).
 */
export function createMutationErrorHandler() {
  return (
    error: unknown,
    _variables: unknown,
    _context: unknown,
    mutation: Mutation<unknown, unknown, unknown, unknown>,
  ) => {
    const meta = mutation.meta;
    if (meta?.suppressErrorToast) return;
    const label =
      typeof meta?.errorLabel === "string"
        ? meta.errorLabel
        : "Something went wrong";
    toasts.error(label, error);
  };
}
