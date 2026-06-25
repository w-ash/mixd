/**
 * App-global "what's running right now" source for workflow runs.
 *
 * Backed by the shared {@link useAdaptivePollingList}: one DB-backed query
 * (`GET /workflows/active-runs`) is the single source of truth for in-flight
 * runs across every workflow. The workflow detail page selects it to one
 * workflow to reconnect to a live run after reload. The execution context also
 * invalidates this query on run start/complete, so the in-tab path updates
 * instantly; the poll only catches runs started elsewhere (another tab, the
 * scheduler).
 */

import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import {
  getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey,
  listActiveRunsApiV1WorkflowsActiveRunsGet,
} from "#/api/generated/workflows/workflows";
import { useAdaptivePollingList } from "#/hooks/useAdaptivePollingList";

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
 * The single active run for one workflow, or null. At most one exists — the
 * `uq_workflow_runs_active` partial unique index guarantees ≤1 active run per
 * workflow — so this is unambiguous.
 */
export function useActiveRun(workflowId: string) {
  return useAdaptivePollingList({
    queryKey: getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey(),
    queryFn: async () =>
      runsFromResponse(await listActiveRunsApiV1WorkflowsActiveRunsGet()),
    select: (runs) => runs.find((r) => r.workflow_id === workflowId) ?? null,
  });
}
