/** Shared SSE event types used across workflow execution, preview, and progress hooks. */

import {
  SseEventName,
  type SseNodeStatusEvent,
  type SseOperationProgressEvent,
  type SseOperationStartedEvent,
  type SseOperationTerminalEvent,
  type SsePreviewCompleteEvent,
  type SseRunAcceptedEvent,
  type SseSubOperationCompletedEvent,
  type SseSubOperationStartedEvent,
  type SseSubProgressEvent,
} from "#/api/generated/model";
import type { SSEEvent } from "#/api/sse-client";

/** Wire names for the SSE events the API emits, from the generated schema. */
export const SSE_EVENT = {
  RUN_ACCEPTED: SseEventName.run_accepted,
  NODE_STATUS: SseEventName.node_status,
  STARTED: SseEventName.started,
  PROGRESS: SseEventName.progress,
  SUB_OPERATION_STARTED: SseEventName.sub_operation_started,
  SUB_PROGRESS: SseEventName.sub_progress,
  SUB_OPERATION_COMPLETED: SseEventName.sub_operation_completed,
  COMPLETE: SseEventName.complete,
  PREVIEW_COMPLETE: SseEventName.preview_complete,
  ERROR: SseEventName.error,
} as const;

/**
 * Payload each event name carries.
 *
 * Mirrors `SSE_EVENT_SCHEMAS` in `src/interface/api/schemas/sse_events.py`:
 * `complete` and `error` share one model because the two frames differ only in
 * which optional fields the producer fills. Optional keys can be absent — the
 * emitters serialize with `exclude_unset`.
 */
export interface SSEEventPayloads {
  run_accepted: SseRunAcceptedEvent;
  node_status: SseNodeStatusEvent;
  started: SseOperationStartedEvent;
  progress: SseOperationProgressEvent;
  sub_operation_started: SseSubOperationStartedEvent;
  sub_progress: SseSubProgressEvent;
  sub_operation_completed: SseSubOperationCompletedEvent;
  complete: SseOperationTerminalEvent;
  preview_complete: SsePreviewCompleteEvent;
  error: SseOperationTerminalEvent;
}

/** One decoded frame, discriminated on the SSE event name. */
export type SSEDomainEvent = {
  [K in keyof SSEEventPayloads]: { event: K; data: SSEEventPayloads[K] };
}[keyof SSEEventPayloads];

const KNOWN_EVENTS: ReadonlySet<string> = new Set(Object.values(SseEventName));

/**
 * Decode a transport frame into a typed domain event.
 *
 * Returns null for a keepalive, an unknown event name or malformed JSON — all
 * of which the stream survives. Field-level validation is the backend's job:
 * every payload is built from a strict Pydantic model at its emitter.
 */
export function parseSSEEvent(raw: SSEEvent): SSEDomainEvent | null {
  if (!raw.data || !KNOWN_EVENTS.has(raw.event)) return null;
  try {
    return {
      event: raw.event,
      data: JSON.parse(raw.data),
    } as SSEDomainEvent;
  } catch {
    return null;
  }
}

/** The four states the canvas styles a node with. */
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

/** Snake_case node state: a `node_status` frame, or the equivalent row from an
 *  operation snapshot (whose `status` spans the wider run vocabulary). */
export type NodeStatusSource = Omit<SseNodeStatusEvent, "status"> & {
  status: string;
};

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
