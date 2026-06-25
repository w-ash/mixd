/**
 * Shared adaptive-polling list query.
 *
 * One cache entry (keyed by `queryKey`) fanned out to many consumers via
 * per-call `select`, polling fast while the list is non-empty and slow when
 * idle — the single "what's running right now" pattern behind both workflow
 * runs (`useActiveRun`) and import/sync operations (`useActiveOperations`).
 * The `queryFn` returns already-unwrapped rows, so the cache stores domain
 * rows and `refetchInterval` reads their count directly.
 *
 * Config follows TanStack Query v5 best practice for an interval-polled query:
 *   - the interval is the freshness contract, so polling PAUSES while the tab is
 *     hidden (the v5 default — we deliberately omit `refetchIntervalInBackground`
 *     so abandoned/background tabs don't poll forever),
 *   - `staleTime` is the active cadence so focus/mount/reconnect don't stack an
 *     extra fetch on top of the interval, and
 *   - `enabled` gates polling on a precondition (e.g. unauthenticated → off),
 *     which also stops the `refetchInterval` timer entirely.
 */

import { type QueryKey, useQuery } from "@tanstack/react-query";

/** Poll cadence while at least one item is active. */
const ACTIVE_POLL_MS = 5_000;
/** Poll cadence when nothing is running — just enough to notice cross-tab starts. */
const IDLE_POLL_MS = 25_000;

export interface AdaptivePollingListOptions<TItem, TData> {
  queryKey: QueryKey;
  /** Fetch + unwrap the envelope to the row array (empty on any non-200). */
  queryFn: () => Promise<TItem[]>;
  /** Derive this consumer's view from the rows (default: the rows themselves). */
  select?: (items: TItem[]) => TData;
  /** Gate polling on a precondition (default true). */
  enabled?: boolean;
}

export function useAdaptivePollingList<TItem, TData = TItem[]>({
  queryKey,
  queryFn,
  select,
  enabled = true,
}: AdaptivePollingListOptions<TItem, TData>) {
  return useQuery({
    queryKey,
    queryFn,
    select,
    enabled,
    staleTime: ACTIVE_POLL_MS,
    refetchInterval: (query) =>
      (query.state.data?.length ?? 0) > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS,
  });
}
