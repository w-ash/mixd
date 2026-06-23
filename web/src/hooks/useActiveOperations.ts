/**
 * App-global "which imports/syncs are running right now" source.
 *
 * Mirrors {@link useActiveRuns} (the workflow-run equivalent): one DB-backed
 * query (`GET /operation-runs?status=running`) is the single source of truth,
 * shared across every consumer via the same cache entry — the sidebar badge and
 * the operations provider both read it with their own `select`, so there is
 * exactly one request and one poll regardless of how many subscribe.
 *
 * Polling is adaptive: fast while something is in flight, slow when idle.
 */

import { useQuery } from "@tanstack/react-query";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  getListOperationRunsApiV1OperationRunsGetQueryKey,
  listOperationRunsApiV1OperationRunsGet,
} from "#/api/generated/operation-runs/operation-runs";

/** Poll cadence while at least one operation is active. */
const ACTIVE_POLL_MS = 5_000;
/** Poll cadence when nothing is running — just enough to notice cross-tab starts. */
const IDLE_POLL_MS = 25_000;

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
 * Shared base query. Both public hooks call this with their own `select`, so
 * they observe the same cache entry (same queryKey) — one fetch, one poll.
 */
function useActiveOperationsQuery<TData>(
  select: (ops: OperationRunSummarySchema[]) => TData,
) {
  return useQuery({
    queryKey: getListOperationRunsApiV1OperationRunsGetQueryKey(RUNNING_PARAMS),
    queryFn: () => listOperationRunsApiV1OperationRunsGet(RUNNING_PARAMS),
    select: (resp: OperationRunsResponse) =>
      select(operationsFromResponse(resp)),
    staleTime: 0,
    refetchIntervalInBackground: true,
    refetchInterval: (query) =>
      operationsFromResponse(query.state.data).length > 0
        ? ACTIVE_POLL_MS
        : IDLE_POLL_MS,
  });
}

/** All of the user's in-flight import/sync operations. */
export function useActiveOperations() {
  return useActiveOperationsQuery((ops) => ops);
}

/** The single in-flight operation matching `operationId`, or null. */
export function useActiveOperation(operationId: string) {
  return useActiveOperationsQuery(
    (ops) => ops.find((o) => o.operation_id === operationId) ?? null,
  );
}
