/**
 * Hook for executing a workflow and tracking per-node status via SSE.
 *
 * Manages the full lifecycle: trigger mutation → connect SSE → process
 * node_status events → update nodeStatuses map → invalidate queries on completion.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey,
  getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey,
  getListWorkflowsApiV1WorkflowsGetQueryKey,
  useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost,
} from "@/api/generated/workflows/workflows";
import { connectToSSE, type SSEEvent } from "@/api/sse-client";

export type NodeExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface NodeStatus {
  nodeId: string;
  nodeType: string;
  status: NodeExecutionStatus;
  executionOrder: number;
  totalNodes: number;
  durationMs?: number;
  inputTrackCount?: number;
  outputTrackCount?: number;
  errorMessage?: string;
}

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
  const [nodeStatuses, setNodeStatuses] = useState<Map<string, NodeStatus>>(
    new Map(),
  );
  const [error, setError] = useState<Error | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  const mutation = useRunWorkflowEndpointApiV1WorkflowsWorkflowIdRunPost();

  const cleanup = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }, []);

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

  // SSE event processing — connects when operationId is set
  useEffect(() => {
    if (!operationId) return;

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const processEvent = (event: SSEEvent) => {
      if (!event.data) return;

      try {
        const data = JSON.parse(event.data);

        switch (event.event) {
          case "node_status":
            setNodeStatuses((prev) => {
              const next = new Map(prev);
              next.set(data.node_id, {
                nodeId: data.node_id,
                nodeType: data.node_type ?? "",
                status: data.status,
                executionOrder: data.execution_order ?? 0,
                totalNodes: data.total_nodes ?? 0,
                durationMs: data.duration_ms,
                inputTrackCount: data.input_track_count,
                outputTrackCount: data.output_track_count,
                errorMessage: data.error_message,
              });
              return next;
            });
            break;

          case "complete":
            setIsExecuting(false);
            invalidateWorkflowQueries();
            cleanup();
            break;

          case "error":
            setIsExecuting(false);
            setError(new Error(data.error_message ?? "Workflow failed"));
            invalidateWorkflowQueries();
            cleanup();
            break;
        }
      } catch {
        // Ignore malformed events
      }
    };

    (async () => {
      try {
        const events = await connectToSSE(
          `/api/v1/operations/${operationId}/progress`,
          ctrl.signal,
        );
        for await (const event of events) {
          processEvent(event);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err : new Error("SSE connection error"),
        );
        setIsExecuting(false);
      }
    })();

    return cleanup;
  }, [operationId, invalidateWorkflowQueries, cleanup]);

  const execute = useCallback(() => {
    setError(null);
    setNodeStatuses(new Map());

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
          setError(
            err instanceof Error ? err : new Error("Failed to start workflow"),
          );
          toast.error("Failed to start workflow", {
            description:
              err instanceof Error ? err.message : "An error occurred",
          });
        },
      },
    );
  }, [mutation, workflowId]);

  return {
    isExecuting,
    operationId,
    runId,
    nodeStatuses,
    error,
    execute,
  };
}
