/**
 * Hook for executing a workflow and tracking per-node status via SSE.
 *
 * Thin facade over WorkflowExecutionContext — the context owns the SSE
 * connection and node statuses so they survive navigation between pages.
 */

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost } from "@/api/generated/workflows/workflows";
import { useWorkflowExecutionContext } from "@/contexts/WorkflowExecutionContext";
import type { NodeStatus } from "@/lib/sse-types";

/** Referentially-stable empty map for non-matching workflows. */
const EMPTY_MAP: Map<string, NodeStatus> = new Map();

export interface UseWorkflowExecutionReturn {
  isExecuting: boolean;
  operationId: string | null;
  runId: number | null;
  nodeStatuses: Map<string, NodeStatus>;
  error: Error | null;
  execute: () => void;
}

export function useWorkflowExecution(
  workflowId: number,
): UseWorkflowExecutionReturn {
  const ctx = useWorkflowExecutionContext();
  const mutation = useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost();
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
              run_id: number;
            };
            ctx.startExecution(workflowId, data.operation_id, data.run_id);
          } else {
            toast.error("Failed to start workflow");
          }
        },
        onError: (err) => {
          const error =
            err instanceof Error ? err : new Error("Failed to start workflow");
          setMutationError(error);
          toast.error("Failed to start workflow", {
            description: error.message,
          });
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
      error: mutationError,
      execute,
    };
  }

  return {
    isExecuting: ctx.isExecuting,
    operationId: ctx.operationId,
    runId: ctx.runId,
    nodeStatuses: ctx.nodeStatuses,
    error: mutationError ?? ctx.error,
    execute,
  };
}
