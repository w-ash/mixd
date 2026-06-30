/**
 * Workflow-execution view over the shared `useOperationSSE` core.
 *
 * Adds the workflow-specific reductions — a `node_status` Map (`useNodeStatuses`),
 * `runAccepted`, the latest `sub_progress`, and the `final_status==="cancelled"`
 * graceful-terminal rule — while the core owns the SSE transport, the terminal
 * idempotency latch, and the re-attach/stall recovery gate.
 *
 * Recovery source: `GET /operations/{id}/snapshot` (workflow_runs + nodes). On
 * adopt() or a 45 s SSE stall the core opens `recovery.active`; this wrapper
 * enables the snapshot query on it, merges persisted node state, and reports a
 * terminal snapshot back through `core.reportTerminal()` (so a delayed live SSE
 * terminal in the same tick can't double-fire).
 */

import { useEffect, useRef, useState } from "react";

import { useNodeStatuses } from "#/hooks/useNodeStatuses";
import {
  isTerminalSnapshot,
  type OperationSnapshot,
  useOperationSnapshot,
} from "#/hooks/useOperationSnapshot";
import { useOperationSSE } from "#/hooks/useOperationSSE";
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
  /**
   * Like `start`, but for re-attaching to a run that's already in flight (e.g.
   * after a page reload). Seeds current state from the DB snapshot immediately
   * instead of waiting for an SSE stall, then streams live where available.
   */
  adopt: (operationId: string) => void;
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

  const [domainError, setDomainError] = useState<Error | null>(null);
  const [runAccepted, setRunAccepted] = useState(false);
  const [subProgress, setSubProgress] = useState<SubProgressUpdate | null>(
    null,
  );
  // Set when the SSE stream closes WITHOUT a terminal frame (server crash, proxy
  // drop). Opens the snapshot reconcile below to learn the run's real terminal
  // state via REST — a clean close doesn't fire the watchdog's `stalled`, so the
  // recovery gate wouldn't otherwise open.
  const [streamEnded, setStreamEnded] = useState(false);

  const {
    nodeStatuses,
    handleNodeStatusEvent,
    mergeNodeStatusEvents,
    resetNodeStatuses,
  } = useNodeStatuses();

  // Hold caller config/callbacks in refs so the core's event handler (called
  // during SSE delivery, after render) reads the latest without re-subscribing.
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const completionEventsRef = useRef(completionEvents);
  completionEventsRef.current = completionEvents;
  const errorFallbackRef = useRef(errorFallbackMessage);
  errorFallbackRef.current = errorFallbackMessage;

  const core = useOperationSSE({
    onReset: () => {
      setDomainError(null);
      setRunAccepted(false);
      setSubProgress(null);
      setStreamEnded(false);
      resetNodeStatuses();
    },
    onStreamEnd: () => setStreamEnded(true),
    onDomainEvent(eventType, d, reportTerminal) {
      switch (eventType) {
        case SSE_EVENT.RUN_ACCEPTED: {
          setRunAccepted(true);
          return;
        }
        case SSE_EVENT.NODE_STATUS: {
          handleNodeStatusEvent(d);
          return;
        }
        case SSE_EVENT.SUB_PROGRESS: {
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
          const subOperationId = String(d.operation_id ?? "");
          setSubProgress((prev) =>
            prev?.subOperationId === subOperationId ? null : prev,
          );
          return;
        }
        case SSE_EVENT.ERROR: {
          // The terminal event carries the real run status (final_status). A
          // `cancelled` run is a graceful, orderly stop (e.g. SIGTERM drain on
          // deploy/autoscale), not a failure — resolve it as a terminal
          // completion so the UI shows a neutral "Cancelled" badge rather than a
          // spurious error. failed/crashed remain errors.
          const finalStatus = d.final_status as string | undefined;
          if (finalStatus === "cancelled") {
            if (reportTerminal()) onCompleteRef.current?.("cancelled", d);
          } else if (reportTerminal()) {
            setDomainError(
              new Error(
                (d.error_message as string) ?? errorFallbackRef.current,
              ),
            );
            onErrorRef.current?.();
          }
          return;
        }
        default: {
          if (completionEventsRef.current.has(eventType) && reportTerminal()) {
            onCompleteRef.current?.(eventType, d);
          }
        }
      }
    },
  });

  // REST-based recovery: poll the snapshot endpoint while the core's recovery
  // gate is open (seed-on-adopt, then stall-only). Stops automatically once a
  // seed is consumed and SSE is healthy.
  const { reportTerminal } = core;
  const { active: recoveryActive, markSeeded } = core.recovery;
  // `core.isRunning` gates the streamEnded path so the resolved snapshot's
  // reportTerminal (which clears isRunning) stops the poll.
  const snapshotQuery = useOperationSnapshot(core.operationId, {
    enabled: recoveryActive || (streamEnded && core.isRunning),
  });

  useEffect(() => {
    const snapshot: OperationSnapshot | undefined = snapshotQuery.data;
    if (!snapshot) return;

    // First snapshot after an adopt() is the reconnect seed — once merged we
    // have the real current state, so drop the gate back to stall-only.
    markSeeded();

    // Reconcile persisted node states into the in-memory map in a single
    // setState call — no N intermediate Map allocations. Default a missing
    // `nodes` to empty: a recovery snapshot that arrives partial (or a late
    // fetch resolving against a torn-down mock in tests) makes the merge a safe
    // no-op rather than throwing, while the terminal-status check below still runs.
    const nodes = snapshot.nodes ?? [];
    const totalNodes = nodes.length;
    mergeNodeStatusEvents(
      nodes.map((node) => ({
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
      // Sweeper-marked runs surface here when the SSE terminal event was lost.
      // "failed"/"crashed" resolve to a terminal error; "cancelled" is an
      // orderly stop (neutral badge) — matching the live ERROR path above.
      if (snapshot.status === "failed" || snapshot.status === "crashed") {
        if (reportTerminal()) {
          setDomainError(
            new Error(snapshot.error_message ?? errorFallbackRef.current),
          );
          onErrorRef.current?.();
        }
      } else if (reportTerminal()) {
        onCompleteRef.current?.(snapshot.status, snapshot);
      }
    }
  }, [snapshotQuery.data, mergeNodeStatusEvents, reportTerminal, markSeeded]);

  return {
    operationId: core.operationId,
    isRunning: core.isRunning,
    nodeStatuses,
    runAccepted,
    subProgress,
    error: domainError ?? core.error,
    sseState: core.sseState,
    lastEventAt: core.lastEventAt,
    start: core.start,
    adopt: core.adopt,
    reset: core.reset,
    disconnect: core.disconnect,
  };
}
