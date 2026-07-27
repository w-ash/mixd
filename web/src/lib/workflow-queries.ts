/**
 * Cache reconciliation for every workflow mutation and run-state transition.
 *
 * Two rules, matching Tanstack Query's own guidance:
 *
 *  - **Detail keys are written**, not invalidated. A save returns the saved
 *    workflow, so `setQueryData` renders it with no refetch and no stale
 *    window — the user sees what they just saved, immediately.
 *  - **List keys are invalidated.** Row order, `updated_at`, and the run count
 *    are all server-derived, so refetching is safer than patching in place.
 *
 * Orval keys are opaque URL strings: `["/api/v1/workflows"]` prefix-matches
 * every paginated list variant, but does NOT reach `["/api/v1/workflows/<id>"]`.
 * Detail invalidation is therefore always explicit.
 */

import type { QueryClient } from "@tanstack/react-query";

import type { WorkflowDetailSchema } from "#/api/generated/model";
import {
  getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey,
  getGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGetQueryKey,
  getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey,
  getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey,
  getListWorkflowsApiV1WorkflowsGetQueryKey,
  getListWorkflowVersionsApiV1WorkflowsWorkflowIdVersionsGetQueryKey,
} from "#/api/generated/workflows/workflows";

export function invalidateWorkflowList(queryClient: QueryClient) {
  void queryClient.invalidateQueries({
    queryKey: getListWorkflowsApiV1WorkflowsGetQueryKey(),
  });
}

export function invalidateWorkflowDetail(
  queryClient: QueryClient,
  workflowId: string,
) {
  void queryClient.invalidateQueries({
    queryKey: getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(workflowId),
  });
}

export function invalidateWorkflowVersions(
  queryClient: QueryClient,
  workflowId: string,
) {
  void queryClient.invalidateQueries({
    queryKey:
      getListWorkflowVersionsApiV1WorkflowsWorkflowIdVersionsGetQueryKey(
        workflowId,
      ),
  });
}

export function invalidateWorkflowRuns(
  queryClient: QueryClient,
  workflowId: string,
) {
  void queryClient.invalidateQueries({
    queryKey:
      getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey(workflowId),
  });
}

export function invalidateActiveRuns(queryClient: QueryClient) {
  void queryClient.invalidateQueries({
    queryKey: getListActiveRunsApiV1WorkflowsActiveRunsGetQueryKey(),
  });
}

/**
 * Seed the detail cache from a mutation response.
 *
 * `customFetch` wraps every response as `{data, status, headers}` and that whole
 * envelope is what lands in the cache, so the write has to reconstruct it —
 * writing the bare entity type-checks but reads back as `undefined` everywhere.
 * The status is normalised to 200 because callers may be handing us a 201.
 */
export function writeWorkflowDetail(
  queryClient: QueryClient,
  workflowId: string,
  workflow: WorkflowDetailSchema,
  headers: Headers,
) {
  queryClient.setQueryData(
    getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(workflowId),
    { data: workflow, status: 200 as const, headers },
  );
}

/**
 * A workflow was created, updated, or reverted.
 *
 * The returned workflow goes straight into the detail cache so the next screen
 * renders it without a fetch; the list and version history refetch in the
 * background because both are server-derived.
 */
export function afterWorkflowSaved(
  queryClient: QueryClient,
  workflowId: string,
  workflow: WorkflowDetailSchema,
  headers: Headers,
) {
  writeWorkflowDetail(queryClient, workflowId, workflow, headers);
  invalidateWorkflowList(queryClient);
  invalidateWorkflowVersions(queryClient, workflowId);
}

/**
 * A run started or reached a terminal state.
 *
 * Fires on BOTH edges. On start this flips every mounted surface to "running"
 * without waiting for the 25s active-runs poll; on terminal it refreshes the
 * persisted run rows. `runId` is only known at terminal, and is what makes an
 * open run-detail page reconcile instead of staying frozen.
 */
export function afterRunStateChanged(
  queryClient: QueryClient,
  workflowId: string,
  runId?: string | null,
) {
  invalidateWorkflowRuns(queryClient, workflowId);
  invalidateWorkflowDetail(queryClient, workflowId);
  invalidateWorkflowList(queryClient);
  invalidateActiveRuns(queryClient);
  if (runId) {
    void queryClient.invalidateQueries({
      queryKey: getGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGetQueryKey(
        workflowId,
        runId,
      ),
    });
  }
}
