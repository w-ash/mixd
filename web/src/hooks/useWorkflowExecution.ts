/**
 * Hook for executing a workflow and tracking per-node status via SSE.
 *
 * Manages the full lifecycle: trigger mutation → connect SSE → process
 * node_status events → update nodeStatuses map → invalidate queries on completion.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import {
  getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey,
  getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey,
  getListWorkflowsApiV1WorkflowsGetQueryKey,
  useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost,
} from "@/api/generated/workflows/workflows";

import { useNodeStatuses } from "@/hooks/useNodeStatuses";
import { useSSEConnection } from "@/hooks/useSSEConnection";
import type { NodeStatus } from "@/lib/sse-types";

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
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [domainError, setDomainError] = useState<Error | null>(null);

  const queryClient = useQueryClient();
  const mutation = useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost();
  const { nodeStatuses, handleNodeStatusEvent, resetNodeStatuses } =
    useNodeStatuses();

  const invalidateWorkflowQueries = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey:
        getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey(workflowId),
    });
    queryClient.invalidateQueries({
      queryKey: getListWorkflowsApiV1WorkflowsGetQueryKey(),
    });
    queryClient.invalidateQueries({
      queryKey: getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(workflowId),
    });
  }, [queryClient, workflowId]);

  const { error: sseError, disconnect } = useSSEConnection(operationId, {
    onEvent(eventType, data) {
      switch (eventType) {
        case "node_status":
          handleNodeStatusEvent(data);
          break;

        case "complete":
          setIsExecuting(false);
          invalidateWorkflowQueries();
          disconnect();
          break;

        case "error": {
          const d = data as Record<string, unknown>;
          setIsExecuting(false);
          setDomainError(
            new Error((d.error_message as string) ?? "Workflow failed"),
          );
          invalidateWorkflowQueries();
          disconnect();
          break;
        }
      }
    },
  });

  const execute = useCallback(() => {
    setDomainError(null);
    resetNodeStatuses();

    mutation.mutate(
      { workflowId },
      {
        onSuccess: (res) => {
          if (res.status === 202) {
            const data = res.data as {
              operation_id: string;
              run_id: number;
            };
            setOperationId(data.operation_id);
            setRunId(data.run_id);
            setIsExecuting(true);
          } else {
            toast.error("Failed to start workflow");
          }
        },
        onError: (err) => {
          setDomainError(
            err instanceof Error ? err : new Error("Failed to start workflow"),
          );
          toast.error("Failed to start workflow", {
            description:
              err instanceof Error ? err.message : "An error occurred",
          });
        },
      },
    );
  }, [mutation, workflowId, resetNodeStatuses]);

  return {
    isExecuting,
    operationId,
    runId,
    nodeStatuses,
    error: domainError ?? sseError,
    execute,
  };
}
