import { MutationCache, QueryClient } from "@tanstack/react-query";

import { createMutationErrorHandler } from "#/lib/toasts";

import { API_ERROR_CODES, ApiError } from "./client";

// Type-register the shape of `meta` on mutations so callers get
// strongly-typed `meta: { errorLabel }` / `meta: { suppressErrorToast }`.
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      /** Toast title shown by the global MutationCache.onError handler. */
      errorLabel?: string;
      /** Skip the global toast (caller shows an inline error instead). */
      suppressErrorToast?: boolean;
    };
  }
}

/** Named staleTime presets by data volatility. */
export const STALE = {
  /** Tracks, active data — matches the global default (30s) */
  FAST: 30_000,
  /** Playlists (1 min) */
  MEDIUM: 60_000,
  /** Workflows (2 min) */
  SLOW: 2 * 60_000,
  /** Dashboard stats, connectors (5 min) */
  STATIC: 5 * 60_000,
} as const;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: createMutationErrorHandler(),
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            // Don't retry infrastructure errors — they won't self-resolve
            if (
              error.code === API_ERROR_CODES.DATABASE_UNAVAILABLE ||
              error.code === API_ERROR_CODES.CONNECTOR_NOT_AVAILABLE
            ) {
              return false;
            }
            return error.status >= 500 && failureCount < 2;
          }
          return false;
        },
      },
    },
  });
}
