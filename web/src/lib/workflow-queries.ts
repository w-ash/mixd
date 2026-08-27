/**
 * The two cache reconciliations tags cannot express for workflows.
 *
 * Everything else — list, versions, run rows — is invalidated from the tags the
 * generated mutations carry. What remains is the optimistic detail write (a save
 * returns the saved workflow, so the next screen renders it with no fetch) and
 * the run-state edges, which fire from SSE frames rather than from a mutation.
 */

import type { QueryClient } from "@tanstack/react-query";

import { invalidateTags } from "#/api/cache-tags";
import type { WorkflowDetailSchema } from "#/api/generated/model";
import { getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey } from "#/api/generated/workflows/workflows";

/**
 * Seed the detail cache from a mutation response.
 *
 * `customFetch` wraps every response as `{data, status, headers}` and that whole
 * envelope is what lands in the cache, so the write has to reconstruct it —
 * writing the bare entity type-checks but reads back as `undefined` everywhere.
 * The status is normalised to 200 because callers may be handing us a 201.
 */
export async function writeWorkflowDetail(
  queryClient: QueryClient,
  workflowId: string,
  workflow: WorkflowDetailSchema,
  headers: Headers,
) {
  const queryKey =
    getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(workflowId);
  // A save's route tag is the `workflows` family, which the mounted detail
  // query depends on too — and query-core runs the global
  // `MutationCache.onSuccess` BEFORE the mutation's own, so a refetch is already
  // in flight by the time we get here and its response would land on top of what
  // we write. Cancelling keeps this seed authoritative without putting a round
  // trip in front of the save.
  await queryClient.cancelQueries({ queryKey });
  queryClient.setQueryData(queryKey, {
    data: workflow,
    status: 200 as const,
    headers,
  });
}

/**
 * A run started or reached a terminal state.
 *
 * Fires on BOTH edges. On start this flips every mounted surface to "running"
 * without waiting for the 25s active-runs poll; on terminal it refreshes the
 * persisted run rows.
 */
export function afterRunStateChanged(queryClient: QueryClient) {
  void invalidateTags(queryClient, ["workflow-runs", "workflows"]);
}
