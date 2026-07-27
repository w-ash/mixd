/**
 * Global context for a single active workflow execution.
 *
 * Lives above the router so SSE connection + node statuses persist
 * across page navigations. Only one workflow runs at a time.
 *
 * Split into two contexts to control re-render cost:
 *   - WorkflowExecutionContext: domain state (workflowId, runId,
 *     isExecuting, error, nodeStatuses). Changes ~10x per run on node
 *     lifecycle events.
 *   - SSELivenessContext: transport liveness (sseState, lastEventAt).
 *     Changes 1x/sec from server keepalive frames.
 *
 * Components subscribing only to liveness (the freshness pill) don't
 * cause domain consumers (PipelineStrip, WorkflowGraph) to re-render
 * every second.
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

import { type SubProgressUpdate, useWorkflowSSE } from "#/hooks/useWorkflowSSE";
import type { NodeStatus, SSEState } from "#/lib/sse-types";
import {
  afterRunStateChanged,
  invalidateActiveRuns,
  invalidateWorkflowList,
} from "#/lib/workflow-queries";

export interface WorkflowExecutionState {
  workflowId: string | null;
  operationId: string | null;
  runId: string | null;
  isExecuting: boolean;
  /** True once the backend has emitted run_accepted for the current run. */
  runAccepted: boolean;
  error: Error | null;
  nodeStatuses: Map<string, NodeStatus>;
  /** Latest sub-operation progress, or null when no sub-op is active. */
  subProgress: SubProgressUpdate | null;
  startExecution: (
    workflowId: string,
    operationId: string,
    runId: string,
  ) => void;
  /**
   * Re-attach to a run already in flight (e.g. after a page reload). Seeds
   * current state from the DB snapshot, then streams live where available.
   * Unlike `startExecution`, this does not begin a new run.
   */
  adoptRun: (workflowId: string, operationId: string, runId: string) => void;
}

export interface SSELivenessState {
  sseState: SSEState;
  lastEventAt: number | null;
}

const WorkflowExecutionContext = createContext<WorkflowExecutionState | null>(
  null,
);

const SSELivenessContext = createContext<SSELivenessState | null>(null);

export function WorkflowExecutionProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const queryClient = useQueryClient();

  // Refs so SSE callbacks don't close over stale values. runId is what lets the
  // terminal reconcile reach an open run-detail page instead of leaving it on
  // the "running" snapshot it first loaded.
  const workflowIdRef = useRef(workflowId);
  workflowIdRef.current = workflowId;
  const runIdRef = useRef(runId);
  runIdRef.current = runId;

  const invalidateWorkflowQueries = useCallback(() => {
    const wfId = workflowIdRef.current;
    if (wfId !== null) {
      afterRunStateChanged(queryClient, wfId, runIdRef.current);
      return;
    }
    // No workflow attached (shouldn't happen — both start paths set it first),
    // but still refresh the app-global sources so nothing is left stuck.
    invalidateWorkflowList(queryClient);
    invalidateActiveRuns(queryClient);
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

  const adoptRun = useCallback(
    (wfId: string, opId: string, rId: string) => {
      setWorkflowId(wfId);
      setRunId(rId);
      sse.adopt(opId);
    },
    [sse.adopt],
  );

  // Domain state — changes on node lifecycle events (~10x per run) plus
  // sub_progress updates throttled at the backend to <= 4 Hz.
  const executionValue = useMemo<WorkflowExecutionState>(
    () => ({
      workflowId,
      operationId: sse.operationId,
      runId,
      isExecuting: sse.isRunning,
      runAccepted: sse.runAccepted,
      error: sse.error,
      nodeStatuses: sse.nodeStatuses,
      subProgress: sse.subProgress,
      startExecution,
      adoptRun,
    }),
    [
      workflowId,
      sse.operationId,
      runId,
      sse.isRunning,
      sse.runAccepted,
      sse.error,
      sse.nodeStatuses,
      sse.subProgress,
      startExecution,
      adoptRun,
    ],
  );

  // Liveness state — changes ~1x/sec from server keepalive frames.
  const livenessValue = useMemo<SSELivenessState>(
    () => ({ sseState: sse.sseState, lastEventAt: sse.lastEventAt }),
    [sse.sseState, sse.lastEventAt],
  );

  return (
    <WorkflowExecutionContext value={executionValue}>
      <SSELivenessContext value={livenessValue}>{children}</SSELivenessContext>
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

export function useSSELivenessContext(): SSELivenessState {
  const ctx = useContext(SSELivenessContext);
  if (!ctx) {
    throw new Error(
      "useSSELivenessContext must be used within WorkflowExecutionProvider",
    );
  }
  return ctx;
}
