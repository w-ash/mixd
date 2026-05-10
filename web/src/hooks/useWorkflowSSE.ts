/**
 * Shared SSE lifecycle hook for workflow execution and preview.
 *
 * Composes useNodeStatuses + useSSEConnection internally and handles
 * the common event dispatch (node_status, error, completion). Consumers
 * configure which events signal completion and provide callbacks for
 * domain-specific side effects.
 */

import { useCallback, useState } from "react";

import { useNodeStatuses } from "#/hooks/useNodeStatuses";
import { useSSEConnection } from "#/hooks/useSSEConnection";
import type { NodeStatus, SSEState } from "#/lib/sse-types";

const DEFAULT_COMPLETION_EVENTS: ReadonlySet<string> = new Set(["complete"]);

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

  const { nodeStatuses, handleNodeStatusEvent, resetNodeStatuses } =
    useNodeStatuses();

  const {
    error: sseError,
    disconnect,
    state: sseState,
    lastEventAt,
  } = useSSEConnection(operationId, {
    onEvent(eventType, data) {
      if (eventType === "run_accepted") {
        setRunAccepted(true);
        return;
      }

      if (eventType === "node_status") {
        handleNodeStatusEvent(data);
        return;
      }

      if (eventType === "error") {
        const d = data as Record<string, unknown>;
        setDomainError(
          new Error((d.error_message as string) ?? errorFallbackMessage),
        );
        setIsRunning(false);
        disconnect();
        onError?.();
        return;
      }

      if (completionEvents.has(eventType)) {
        setIsRunning(false);
        disconnect();
        onComplete?.(eventType, data);
      }
    },
  });

  const start = useCallback(
    (opId: string) => {
      setDomainError(null);
      resetNodeStatuses();
      setRunAccepted(false);
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
    setOperationId(null);
    setIsRunning(false);
  }, [disconnect, resetNodeStatuses]);

  return {
    operationId,
    isRunning,
    nodeStatuses,
    runAccepted,
    error: domainError ?? sseError,
    sseState,
    lastEventAt,
    start,
    reset,
    disconnect,
  };
}
