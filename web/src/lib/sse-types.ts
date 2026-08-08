/** Shared SSE event types used across workflow execution, preview, and progress hooks. */

/** Wire names for the SSE events the workflow runner emits. Mirrors
 *  WorkflowConstants.SSE_EVENT_* on the backend. */
export const SSE_EVENT = {
  RUN_ACCEPTED: "run_accepted",
  NODE_STATUS: "node_status",
  STARTED: "started",
  PROGRESS: "progress",
  SUB_OPERATION_STARTED: "sub_operation_started",
  SUB_PROGRESS: "sub_progress",
  SUB_OPERATION_COMPLETED: "sub_operation_completed",
  COMPLETE: "complete",
  ERROR: "error",
} as const;

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

/**
 * Lifecycle states for an SSE connection.
 *
 * Transport-level (kind=connecting/open-no-events/streaming/stalled/
 * resuming/closed-*) is owned by useSSEConnection. The "stalled"
 * variant is reached when the watchdog (45s default) fires without any
 * frame, including server keepalive comments. lastEventAt is wall-clock
 * Date.now() of the most recent frame of any kind.
 *
 * "resuming" is the bounded retry window: the transport dropped, and the
 * hook is waiting out a short backoff before ONE reconnect that replays
 * from `lastEventId` (sent as the `Last-Event-ID` header). It is not a
 * failure — "closed-error" is only reached once that retry is spent, and
 * even then the run's durable state may still be recoverable via REST.
 */
export type SSEState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "open-no-events"; openedAt: number }
  | { kind: "streaming"; lastEventAt: number }
  | { kind: "stalled"; lastEventAt: number; since: number }
  | {
      kind: "resuming";
      attempt: number;
      lastEventAt: number | null;
      lastEventId: string | null;
    }
  | { kind: "closed-error"; error: Error; lastEventAt: number | null }
  | { kind: "closed-done"; finalAt: number };

export const SSE_STALL_THRESHOLD_MS = 45_000;
export const SSE_WATCHDOG_TICK_MS = 5_000;

/** Backoff before the single resume attempt after a transport drop. */
export const SSE_RESUME_DELAY_MS = 2_000;
/** How many times a dropped stream is retried before it counts as closed. */
export const SSE_MAX_RESUME_ATTEMPTS = 1;
