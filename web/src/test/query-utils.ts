import type { QueryClient } from "@tanstack/react-query";

import { createQueryClient } from "#/api/query-client";

export function createTestQueryClient(): QueryClient {
  // Reuse the production client so tests exercise the real cache wiring — the
  // error-toast handler AND the tag invalidation. Hand-mirroring it here is how
  // a test suite ends up green against behaviour the app doesn't have.
  const client = createQueryClient();
  // Replaces the defaults wholesale, so tests keep staleTime 0 rather than
  // inheriting production's 30s and passing for the wrong reason.
  client.setDefaultOptions({
    queries: { retry: false, gcTime: 0 },
    mutations: { retry: false },
  });
  return client;
}

/**
 * Seed an observer-less query so invalidation is observable on it.
 *
 * The test client runs `gcTime: 0`, which collects a query the moment it has no
 * observer — so a bare `setQueryData` probe would vanish before the assertion.
 */
export function seedQuery(
  queryClient: QueryClient,
  key: readonly unknown[],
  data: unknown = { data: null },
): void {
  queryClient.setQueryDefaults(key, { gcTime: Infinity });
  queryClient.setQueryData(key, data);
}

/**
 * Whether `key`'s cached query has been invalidated.
 *
 * `invalidateQueries` marks every matched query and refetches only the active
 * ones, so a seeded observer-less query is a probe that doesn't care HOW
 * matching is implemented — unlike a spy on the call arguments.
 */
export function wasInvalidated(
  queryClient: QueryClient,
  key: readonly unknown[],
): boolean {
  return queryClient.getQueryState(key)?.isInvalidated === true;
}
