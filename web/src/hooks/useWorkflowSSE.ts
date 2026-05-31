/**
 * Shared SSE lifecycle hook for workflow execution and preview.
 *
 * Composes useNodeStatuses + useSSEConnection internally and handles
 * the common event dispatch (node_status, error, completion). Consumers
 * configure which events signal completion and provide callbacks for
 * domain-specific side effects.
 *
 * When the SSE watchdog flips state to "stalled" (45 s without any
 * frame), the hook polls GET /operations/{id}/snapshot and reconciles
 * persisted run state into nodeStatuses. Terminal snapshots fire the
 * configured completion callbacks with idempotency so a delayed SSE
 * terminal event in the same render tick doesn't double-fire.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useNodeStatuses } from "#/hooks/useNodeStatuses";
import {
  isTerminalSnapshot,
  type OperationSnapshot,
  useOperationSnapshot,
} from "#/hooks/useOperationSnapshot";
import { useSSEConnection } from "#/hooks/useSSEConnection";
import { type NodeStatus, SSE_EVENT, type SSEState } from "#/lib/sse-types";

const DEFAULT_COMPLETION_EVENTS: ReadonlySet<string> = new Set(["complete"]);

/** Snapshot of the most recent sub_progress event, keyed by its sub-op id. */
export interface SubProgressUpdate {
  /** Sub-operation id — distinguishes consecutive sub-ops (lastfm vs spotify enrich). */
  subOperationId: string;
  current: number;
  total: number | null;
  message: string;
  itemsPerSecond: number | null;
  etaSeconds: number | null;
}

export interface UseWorkflowSSEOptions {
  /** Which event types signal completion (default: {"complete"}) */
  completionEvents?: ReadonlySet<string>;
  /** Called when a completion event fires */
  onComplete?: (eventType: string, data: unknown) => void;
  /** Called after an error event is processed */
  onError?: () => void;
  /** Fallback for missing error_message (default: "Operation failed") */
  errorFallbackMessage?: string;
}

export interface UseWorkflowSSEReturn {
  operationId: string | null;
  isRunning: boolean;
  nodeStatuses: Map<string, NodeStatus>;
  /** True once the server has emitted run_accepted (closes the route-to-Prefect silence gap). */
  runAccepted: boolean;
  /** Latest sub-operation progress, or null when no sub-op is active. */
  subProgress: SubProgressUpdate | null;
  error: Error | null;
  /** Discriminated SSE transport state for liveness UIs. */
  sseState: SSEState;
  /** Wall-clock timestamp of most recent SSE frame (incl. keepalives). */
  lastEventAt: number | null;
  start: (operationId: string) => void;
  reset: () => void;
  disconnect: () => void;
}

export function useWorkflowSSE(
  options: UseWorkflowSSEOptions = {},
): UseWorkflowSSEReturn {
  const {
    completionEvents = DEFAULT_COMPLETION_EVENTS,
    onComplete,
    onError,
    errorFallbackMessage = "Operation failed",
  } = options;

  const [operationId, setOperationId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [domainError, setDomainError] = useState<Error | null>(null);
  const [runAccepted, setRunAccepted] = useState(false);
  const [subProgress, setSubProgress] = useState<SubProgressUpdate | null>(
    null,
  );

  // Idempotency guard: terminal events can race between SSE delivery and
  // the snapshot poll. The first one to fire wins; subsequent terminal
  // signals are no-ops.
  const terminalEmittedRef = useRef(false);

  const {
    nodeStatuses,
    handleNodeStatusEvent,
    mergeNodeStatusEvents,
    resetNodeStatuses,
  } = useNodeStatuses();

  // Hold the latest user callbacks in refs so the SSE/snapshot effects
  // don't need to depend on them (and re-trigger if the parent re-renders).
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const fireTerminalComplete = useCallback(
    (eventType: string, data: unknown) => {
      if (terminalEmittedRef.current) return;
      terminalEmittedRef.current = true;
      setIsRunning(false);
      onCompleteRef.current?.(eventType, data);
    },
    [],
  );

  const fireTerminalError = useCallback((errorMessage: string) => {
    if (terminalEmittedRef.current) return;
    terminalEmittedRef.current = true;
    setDomainError(new Error(errorMessage));
    setIsRunning(false);
    onErrorRef.current?.();
  }, []);

  const {
    error: sseError,
    disconnect,
    state: sseState,
    lastEventAt,
  } = useSSEConnection(operationId, {
    onEvent(eventType, data) {
      switch (eventType) {
        case SSE_EVENT.RUN_ACCEPTED: {
          setRunAccepted(true);
          return;
        }
        case SSE_EVENT.NODE_STATUS: {
          handleNodeStatusEvent(data);
          return;
        }
        case SSE_EVENT.SUB_PROGRESS: {
          const d = data as Record<string, unknown>;
          const subOperationId = String(d.operation_id ?? "");
          if (!subOperationId) return;

          setSubProgress({
            subOperationId,
            current: (d.current as number) ?? 0,
            total: (d.total as number | null | undefined) ?? null,
            message: (d.message as string) ?? "",
            itemsPerSecond:
              (d.items_per_second as number | null | undefined) ?? null,
            etaSeconds: (d.eta_seconds as number | null | undefined) ?? null,
          });
          return;
        }
        case SSE_EVENT.SUB_OPERATION_COMPLETED: {
          const d = data as Record<string, unknown>;
          const subOperationId = String(d.operation_id ?? "");
          setSubProgress((prev) =>
            prev?.subOperationId === subOperationId ? null : prev,
          );
          return;
        }
        case SSE_EVENT.ERROR: {
          const d = data as Record<string, unknown>;
          const errorMessage =
            (d.error_message as string) ?? errorFallbackMessage;
          fireTerminalError(errorMessage);
          disconnect();
          return;
        }
        default: {
          if (completionEvents.has(eventType)) {
            fireTerminalComplete(eventType, data);
            disconnect();
          }
        }
      }
    },
  });

  // REST-based fallback: poll the snapshot endpoint while the SSE
  // watchdog reports stalled. Stops automatically when SSE recovers.
  const snapshotQuery = useOperationSnapshot(operationId, {
    enabled: sseState.kind === "stalled" && !terminalEmittedRef.current,
  });

  useEffect(() => {
    const snapshot: OperationSnapshot | undefined = snapshotQuery.data;
    if (!snapshot) return;

    // Reconcile persisted node states into the in-memory map in a single
    // setState call — keeps the pipeline strip in sync after a stall
    // without paying N intermediate Map allocations.
    const totalNodes = snapshot.nodes.length;
    mergeNodeStatusEvents(
      snapshot.nodes.map((node) => ({
        node_id: node.node_id,
        node_type: node.node_type,
        status: node.status,
        execution_order: node.execution_order,
        total_nodes: totalNodes,
        duration_ms: node.duration_ms ?? undefined,
        input_track_count: node.input_track_count ?? undefined,
        output_track_count: node.output_track_count ?? undefined,
        error_message: node.error_message ?? undefined,
      })),
    );

    if (isTerminalSnapshot(snapshot)) {
      // Sweeper-marked runs surface here when the SSE terminal event was
      // lost: the heartbeat sweeper marks a silent row "crashed" server-side
      // after the stale threshold. "failed" (logic error) and "cancelled"
      // also resolve to a terminal error rather than a completion.
      if (
        snapshot.status === "failed" ||
        snapshot.status === "crashed" ||
        snapshot.status === "cancelled"
      ) {
        fireTerminalError(snapshot.error_message ?? errorFallbackMessage);
      } else {
        fireTerminalComplete(snapshot.status, snapshot);
      }
      disconnect();
    }
  }, [
    snapshotQuery.data,
    mergeNodeStatusEvents,
    fireTerminalComplete,
    fireTerminalError,
    disconnect,
    errorFallbackMessage,
  ]);

  const start = useCallback(
    (opId: string) => {
      setDomainError(null);
      resetNodeStatuses();
      setRunAccepted(false);
      setSubProgress(null);
      terminalEmittedRef.current = false;
      setOperationId(opId);
      setIsRunning(true);
    },
    [resetNodeStatuses],
  );

  const reset = useCallback(() => {
    disconnect();
    setDomainError(null);
    resetNodeStatuses();
    setRunAccepted(false);
    setSubProgress(null);
    terminalEmittedRef.current = false;
    setOperationId(null);
    setIsRunning(false);
  }, [disconnect, resetNodeStatuses]);

  return {
    operationId,
    isRunning,
    nodeStatuses,
    runAccepted,
    subProgress,
    error: domainError ?? sseError,
    sseState,
    lastEventAt,
    start,
    reset,
    disconnect,
  };
}
