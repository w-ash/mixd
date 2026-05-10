/**
 * Hook for executing a workflow and tracking per-node status via SSE.
 *
 * Thin facade over WorkflowExecutionContext — the context owns the SSE
 * connection and node statuses so they survive navigation between pages.
 */

import { useCallback, useState } from "react";

import { useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost } from "#/api/generated/workflows/workflows";
import { useWorkflowExecutionContext } from "#/contexts/WorkflowExecutionContext";
import type { SubProgressUpdate } from "#/hooks/useWorkflowSSE";
import type { NodeStatus } from "#/lib/sse-types";
import { toasts } from "#/lib/toasts";

/** Referentially-stable empty map for non-matching workflows. */
const EMPTY_MAP: Map<string, NodeStatus> = new Map();

export interface UseWorkflowExecutionReturn {
  isExecuting: boolean;
  operationId: string | null;
  runId: string | null;
  nodeStatuses: Map<string, NodeStatus>;
  /** True once the server has emitted run_accepted for the active run. */
  runAccepted: boolean;
  /** Latest sub-operation progress, or null when no sub-op is active. */
  subProgress: SubProgressUpdate | null;
  error: Error | null;
  execute: () => void;
}

export function useWorkflowExecution(
  workflowId: string,
): UseWorkflowExecutionReturn {
  const ctx = useWorkflowExecutionContext();
  const mutation = useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost({
    mutation: { meta: { errorLabel: "Failed to start workflow" } },
  });
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const isThisWorkflow = ctx.workflowId === workflowId;

  const execute = useCallback(() => {
    setMutationError(null);

    mutation.mutate(
      { workflowId },
      {
        onSuccess: (res) => {
          if (res.status === 202) {
            const data = res.data as {
              operation_id: string;
              run_id: string;
            };
            ctx.startExecution(workflowId, data.operation_id, data.run_id);
          } else {
            toasts.message("Failed to start workflow");
          }
        },
        onError: (err) => {
          setMutationError(
            err instanceof Error ? err : new Error("Failed to start workflow"),
          );
          // Toast fires from MutationCache.onError via meta.errorLabel.
        },
      },
    );
  }, [mutation.mutate, workflowId, ctx.startExecution]);

  if (!isThisWorkflow) {
    return {
      isExecuting: false,
      operationId: null,
      runId: null,
      nodeStatuses: EMPTY_MAP,
      runAccepted: false,
      subProgress: null,
      error: mutationError,
      execute,
    };
  }

  return {
    isExecuting: ctx.isExecuting,
    operationId: ctx.operationId,
    runId: ctx.runId,
    nodeStatuses: ctx.nodeStatuses,
    runAccepted: ctx.runAccepted,
    subProgress: ctx.subProgress,
    error: mutationError ?? ctx.error,
    execute,
  };
}
