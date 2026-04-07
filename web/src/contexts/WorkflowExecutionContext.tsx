/**
 * Global context for a single active workflow execution.
 *
 * Lives above the router so SSE connection + node statuses persist
 * across page navigations. Only one workflow runs at a time.
 */

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey,
  getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey,
  getListWorkflowsApiV1WorkflowsGetQueryKey,
} from "#/api/generated/workflows/workflows";
import { useWorkflowSSE } from "#/hooks/useWorkflowSSE";
import type { NodeStatus } from "#/lib/sse-types";

export interface WorkflowExecutionState {
  workflowId: string | null;
  operationId: string | null;
  runId: string | null;
  isExecuting: boolean;
  error: Error | null;
  nodeStatuses: Map<string, NodeStatus>;
  startExecution: (
    workflowId: string,
    operationId: string,
    runId: string,
  ) => void;
}

const WorkflowExecutionContext = createContext<WorkflowExecutionState | null>(
  null,
);

export function WorkflowExecutionProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const queryClient = useQueryClient();

  // Use a ref for workflowId so SSE callbacks don't close over stale values
  const workflowIdRef = useRef(workflowId);
  workflowIdRef.current = workflowId;

  const invalidateWorkflowQueries = useCallback(() => {
    const wfId = workflowIdRef.current;
    if (wfId !== null) {
      queryClient.invalidateQueries({
        queryKey:
          getListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGetQueryKey(wfId),
      });
      queryClient.invalidateQueries({
        queryKey: getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(wfId),
      });
    }
    queryClient.invalidateQueries({
      queryKey: getListWorkflowsApiV1WorkflowsGetQueryKey(),
    });
  }, [queryClient]);

  const sse = useWorkflowSSE({
    errorFallbackMessage: "Workflow failed",
    onComplete: () => invalidateWorkflowQueries(),
    onError: () => invalidateWorkflowQueries(),
  });

  const startExecution = useCallback(
    (wfId: string, opId: string, rId: string) => {
      setWorkflowId(wfId);
      setRunId(rId);
      sse.start(opId);
    },
    [sse.start],
  );

  const value = useMemo<WorkflowExecutionState>(
    () => ({
      workflowId,
      operationId: sse.operationId,
      runId,
      isExecuting: sse.isRunning,
      error: sse.error,
      nodeStatuses: sse.nodeStatuses,
      startExecution,
    }),
    [
      workflowId,
      sse.operationId,
      runId,
      sse.isRunning,
      sse.error,
      sse.nodeStatuses,
      startExecution,
    ],
  );

  return (
    <WorkflowExecutionContext value={value}>
      {children}
    </WorkflowExecutionContext>
  );
}

export function useWorkflowExecutionContext(): WorkflowExecutionState {
  const ctx = useContext(WorkflowExecutionContext);
  if (!ctx) {
    throw new Error(
      "useWorkflowExecutionContext must be used within WorkflowExecutionProvider",
    );
  }
  return ctx;
}
