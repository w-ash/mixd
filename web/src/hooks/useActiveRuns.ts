/**
 * App-global "what's running right now" source for workflow runs.
 *
 * Backed by the shared {@link useAdaptivePollingList}: one DB-backed query
 * (`GET /workflows/active-runs`) is the single source of truth for in-flight
 * runs across every workflow. The detail page selects it down to one workflow
 * to reconnect to a live run after reload; the list page selects it to a set of
 * IDs to mark rows as running.
 *
 * Invalidated on both edges of a run (start and terminal) by
 * `afterRunStateChanged`, so the in-tab path updates instantly. The poll exists
 * only to catch runs this tab didn't start — the scheduler, or another device.
 */

import { useCallback } from "react";

import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import {
  getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey,
  listActiveRunsApiV1WorkflowsActiveRunsGet,
} from "#/api/generated/workflows/workflows";
import { useAdaptivePollingList } from "#/hooks/useAdaptivePollingList";

type ActiveRunsResponse = Awaited<
  ReturnType<typeof listActiveRunsApiV1WorkflowsActiveRunsGet>
>;

/** Stable empty set so consumers don't see a new reference every render. */
const EMPTY_IDS: ReadonlySet<string> = new Set<string>();

/** Unwrap the paginated envelope to the run array (empty on any non-200). */
function runsFromResponse(
  resp: ActiveRunsResponse | undefined,
): WorkflowRunSummarySchema[] {
  return resp?.status === 200 ? (resp.data.data ?? []) : [];
}

/**
 * Module-level so its identity is stable across renders.
 *
 * Tanstack Query memoises `select` on `(data, selectFn)` — an inline arrow is a
 * new function every render, so the selector re-runs and mints a fresh `Set`
 * even when nothing changed. That new reference then propagates into
 * `WorkflowRow`'s props and defeats its `memo()`.
 */
function toWorkflowIdSet(
  runs: WorkflowRunSummarySchema[],
): ReadonlySet<string> {
  return new Set(runs.map((r) => r.workflow_id));
}

/**
 * The single active run for one workflow, or null. At most one exists — the
 * `uq_workflow_runs_active` partial unique index guarantees ≤1 active run per
 * workflow — so this is unambiguous.
 */
export function useActiveRun(workflowId: string) {
  // Memoised for the same reason `toWorkflowIdSet` is module-level: an inline
  // arrow is a new function each render, so Tanstack re-runs `select` and hands
  // back a fresh reference, which then churns every effect listing the result in
  // its dependency array.
  const select = useCallback(
    (runs: WorkflowRunSummarySchema[]) =>
      runs.find((r) => r.workflow_id === workflowId) ?? null,
    [workflowId],
  );

  return useAdaptivePollingList({
    queryKey: getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey(),
    queryFn: async () =>
      runsFromResponse(await listActiveRunsApiV1WorkflowsActiveRunsGet()),
    select,
  });
}

/**
 * The set of workflow IDs with a run in flight right now, from any source.
 *
 * The list page uses this so a scheduler-triggered run — or one started in
 * another tab — renders as running, which in-tab execution state alone can
 * never know about. Shares the query key (and therefore the cache entry and
 * poll) with {@link useActiveRun}.
 */
export function useActiveRunWorkflowIds(): ReadonlySet<string> {
  const { data } = useAdaptivePollingList({
    queryKey: getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey(),
    queryFn: async () =>
      runsFromResponse(await listActiveRunsApiV1WorkflowsActiveRunsGet()),
    select: toWorkflowIdSet,
  });
  return data ?? EMPTY_IDS;
}
