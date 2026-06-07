/**
 * App-global "what's running right now" source.
 *
 * One DB-backed query (`GET /workflows/active-runs`) is the single source of
 * truth for in-flight runs across every workflow. The workflow detail page
 * reads it (selected to one workflow) to reconnect to a live run after reload;
 * a future sidebar indicator reads the whole list — both share the same cache
 * entry, so there's exactly one network request and one poll regardless of how
 * many consumers subscribe.
 *
 * Polling is adaptive: fast while a run is in flight, slow when idle. The
 * execution context also invalidates this query on run start/complete, so the
 * common in-tab path updates instantly; the background poll only exists to
 * catch runs started elsewhere (another tab, the scheduler).
 */

import { useQuery } from "@tanstack/react-query";
import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import {
  getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey,
  listActiveRunsApiV1WorkflowsActiveRunsGet,
} from "#/api/generated/workflows/workflows";

/** Poll cadence while at least one run is active. */
const ACTIVE_POLL_MS = 5_000;
/** Poll cadence when nothing is running — just enough to notice cross-tab starts. */
const IDLE_POLL_MS = 25_000;

type ActiveRunsResponse = Awaited<
  ReturnType<typeof listActiveRunsApiV1WorkflowsActiveRunsGet>
>;

/** Unwrap the paginated envelope to the run array (empty on any non-200). */
function runsFromResponse(
  resp: ActiveRunsResponse | undefined,
): WorkflowRunSummarySchema[] {
  return resp?.status === 200 ? (resp.data.data ?? []) : [];
}

/**
 * Shared base query. Both public hooks call this with their own `select`, so
 * they observe the same cache entry (same queryKey) — one fetch, one poll —
 * and only differ in how they derive their view of the data.
 */
function useActiveRunsQuery<TData>(
  select: (runs: WorkflowRunSummarySchema[]) => TData,
) {
  return useQuery({
    queryKey: getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey(),
    queryFn: () => listActiveRunsApiV1WorkflowsActiveRunsGet(),
    // `select` runs against the raw envelope; refetchInterval sees the same.
    select: (resp: ActiveRunsResponse) => select(runsFromResponse(resp)),
    staleTime: 0,
    refetchIntervalInBackground: true,
    refetchInterval: (query) =>
      runsFromResponse(query.state.data).length > 0
        ? ACTIVE_POLL_MS
        : IDLE_POLL_MS,
  });
}

/** All of the user's in-flight runs across every workflow. */
export function useActiveRuns() {
  return useActiveRunsQuery((runs) => runs);
}

/**
 * The single active run for one workflow, or null. At most one exists — the
 * `uq_workflow_runs_active` partial unique index guarantees ≤1 active run per
 * workflow — so this is unambiguous.
 */
export function useActiveRun(workflowId: string) {
  return useActiveRunsQuery(
    (runs) => runs.find((r) => r.workflow_id === workflowId) ?? null,
  );
}
