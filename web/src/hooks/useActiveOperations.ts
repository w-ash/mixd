/**
 * App-global "which imports/syncs are running right now" source.
 *
 * Backed by the shared {@link useAdaptivePollingList}: one DB-backed query
 * (`GET /operation-runs?status=running`) is the single source of truth, shared
 * across the sidebar badge and the operations watcher via the same cache entry
 * — one request and one poll regardless of how many subscribe.
 */

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  getListOperationRunsApiV1OperationRunsGetQueryKey,
  listOperationRunsApiV1OperationRunsGet,
} from "#/api/generated/operation-runs/operation-runs";
import { useAdaptivePollingList } from "#/hooks/useAdaptivePollingList";

// `type: "all"` so syncs/applies count toward awareness, not just imports.
const RUNNING_PARAMS = { status: "running", type: "all" } as const;

type OperationRunsResponse = Awaited<
  ReturnType<typeof listOperationRunsApiV1OperationRunsGet>
>;

/** Unwrap the paginated envelope to the rows (empty on any non-200). */
function operationsFromResponse(
  resp: OperationRunsResponse | undefined,
): OperationRunSummarySchema[] {
  return resp?.status === 200 ? (resp.data.data ?? []) : [];
}

/**
 * All of the user's in-flight import/sync operations. Pass `enabled: false`
 * (e.g. while unauthenticated) to suspend polling.
 */
export function useActiveOperations(enabled = true) {
  return useAdaptivePollingList({
    queryKey: getListOperationRunsApiV1OperationRunsGetQueryKey(RUNNING_PARAMS),
    queryFn: async () =>
      operationsFromResponse(
        await listOperationRunsApiV1OperationRunsGet(RUNNING_PARAMS),
      ),
    enabled,
  });
}
