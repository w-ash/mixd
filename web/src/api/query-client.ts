import { MutationCache, QueryClient } from "@tanstack/react-query";

import { createMutationErrorHandler } from "#/lib/toasts";

import type { CacheTag } from "./cache-tags";
import { invalidateTags } from "./cache-tags";
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
      /**
       * Cache tags this write dirties. Stamped from the route by the orval
       * mutator (`cache-tags-mutator.ts`); set explicitly to override — either
       * to name a resource this route doesn't own, or to narrow to one member.
       */
      invalidates?: readonly CacheTag[];
      /** Route template the mutation was generated from. Stamped by the mutator. */
      route?: string;
      /**
       * Hold `mutateAsync` open until the invalidation's refetches land.
       *
       * Off by default, and that default matters: query-core awaits
       * `MutationCache.onSuccess` BEFORE the mutation's own `onSuccess`, so
       * awaiting unconditionally would put a round trip in front of every
       * optimistic dialog close in the app. Opt in only where "the screen must
       * be true before we say so" — the connector connect flows.
       */
      awaitInvalidation?: boolean;
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
  const client = new QueryClient({
    mutationCache: new MutationCache({
      onError: createMutationErrorHandler(),
      // Every write invalidates what it staled, from one place. Returning the
      // promise is what makes `mutateAsync` wait; see `awaitInvalidation`.
      onSuccess: (_data, _variables, _context, mutation) => {
        const meta = mutation.meta;
        if (!meta?.invalidates?.length) return;
        const settled = invalidateTags(client, meta.invalidates);
        if (meta.awaitInvalidation) return settled;
        // Not awaited here, so nothing else would catch a failed refetch.
        void settled.catch(() => undefined);
      },
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
  return client;
}
