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

const RUN_TITLES: Record<RunOperationType, (count: number) => string> = {
  import_lastfm_history: (n) =>
    n > 0
      ? `Imported ${n} ${n === 1 ? "scrobble" : "scrobbles"}`
      : "Import complete",
  import_spotify_likes: (n) =>
    n > 0 ? `Imported ${n} ${n === 1 ? "like" : "likes"}` : "Import complete",
  export_lastfm_likes: (n) =>
    n > 0 ? `Exported ${n} ${n === 1 ? "love" : "loves"}` : "Export complete",
  import_spotify_history: (n) =>
    n > 0
      ? `Imported ${n} ${n === 1 ? "scrobble" : "scrobbles"}`
      : "Import complete",
  import_connector_playlists: (n) =>
    n > 0
      ? `Imported ${n} ${n === 1 ? "playlist" : "playlists"}`
      : "Import complete",
  apply_assignments_bulk: (n) =>
    n > 0
      ? `Applied ${n} ${n === 1 ? "assignment" : "assignments"}`
      : "Apply complete",
};

/** Pick the most relevant count from an OperationRun's counts payload. */
function primaryCount(
  operationType: RunOperationType,
  counts: Record<string, unknown>,
): number {
  // Each operation surfaces its primary count under a different key.
  // Fall back to 0 when the counts payload is empty (e.g., zero-work runs).
  const candidates: Record<RunOperationType, string[]> = {
    import_lastfm_history: [
      "scrobbles_imported",
      "plays_imported",
      "tracks_imported",
    ],
    import_spotify_likes: ["likes_imported", "tracks_imported"],
    export_lastfm_likes: ["loves_exported", "tracks_exported"],
    import_spotify_history: ["scrobbles_imported", "plays_imported"],
    import_connector_playlists: ["playlists_imported", "succeeded"],
    apply_assignments_bulk: ["assignments_processed"],
  };
  for (const key of candidates[operationType]) {
    const v = counts[key];
    if (typeof v === "number") return v;
  }
  return 0;
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
   * The action is omitted when ``runId`` is null (e.g., the audit row
   * couldn't be persisted) so the toast doesn't deep-link nowhere.
   */
  runCompleted({
    operationType,
    counts,
    issueCount,
    runId,
    failed = false,
    onNavigate,
  }: {
    operationType: RunOperationType;
    counts: Record<string, unknown>;
    issueCount: number;
    runId: string | null;
    failed?: boolean;
    onNavigate: (path: string) => void;
  }) {
    const title = RUN_TITLES[operationType](
      primaryCount(operationType, counts),
    );
    const description =
      issueCount > 0
        ? `${issueCount} ${issueCount === 1 ? "item" : "items"} had issues`
        : undefined;
    const action: ToastAction | undefined =
      runId !== null && (issueCount > 0 || failed)
        ? {
            label: "View log",
            onClick: () => onNavigate(`/settings/imports?run=${runId}`),
          }
        : undefined;

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
