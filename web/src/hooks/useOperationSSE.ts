/**
 * Shared SSE lifecycle core for workflow execution and import/sync operations.
 *
 * This is the payload-agnostic spine both `useWorkflowSSE` (node-shaped) and
 * `useOperationProgress` (sub-operation-shaped) compose over. It owns ONLY:
 *   - the SSE transport (`useSSEConnection`) + `operationId`/`isRunning` lifecycle,
 *   - `start` / `adopt` / `reset` / `disconnect`,
 *   - a single first-writer-wins terminal latch (`reportTerminal`), and
 *   - the recovery gate (`recovery.active`) that tells a wrapper *when* to run its
 *     own REST re-attach fetch (snapshot for workflows, operation-run row for
 *     imports) — seed-on-adopt, then stall-only.
 *
 * It deliberately owns NO terminal semantics: the two worlds parse the `error`
 * channel differently (workflows read `final_status`/`error_message` and surface
 * a returned error; operations read `message`/`counts` and set their own progress
 * state). So every parsed frame is handed to the wrapper via `onDomainEvent`,
 * which decides what is terminal and calls `reportTerminal()` to arbitrate.
 */

import { useCallback, useRef, useState } from "react";

import { useSSEConnection } from "#/hooks/useSSEConnection";
import type { SSEState } from "#/lib/sse-types";

/** Re-attach recovery gate. Wrappers enable their REST seed/poll on `active`. */
export interface OperationSSERecovery {
  /**
   * True while a re-attach seed is pending (just adopted) OR the SSE stream is
   * stalled — and no terminal has fired. A wrapper gates its own snapshot/row
   * fetch on this; without a fetch source it can simply ignore it.
   */
  active: boolean;
  /** Drop the seed gate to stall-only once the first re-attach seed is consumed. */
  markSeeded: () => void;
}

export interface UseOperationSSEOptions {
  /**
   * Reduce one parsed SSE event. The wrapper owns all domain + terminal
   * semantics; it calls the supplied `reportTerminal` to arbitrate a terminal
   * (idempotent across the SSE channel and the recovery fetch).
   */
  onDomainEvent: (
    eventType: string,
    data: Record<string, unknown>,
    reportTerminal: () => boolean,
  ) => void;
  /** Clear wrapper-owned domain state. Invoked on every start/adopt/reset. */
  onReset?: () => void;
  /** Called when the SSE stream ends normally (iterator exhausted, not aborted). */
  onStreamEnd?: () => void;
}

export interface UseOperationSSEReturn {
  operationId: string | null;
  isRunning: boolean;
  isConnected: boolean;
  /** Transport error (connection failure). Domain errors are wrapper-owned. */
  error: Error | null;
  sseState: SSEState;
  lastEventAt: number | null;
  recovery: OperationSSERecovery;
  start: (operationId: string) => void;
  /**
   * Re-attach to a run already in flight (e.g. after a reload). Like `start`,
   * but opens the recovery gate so the wrapper can seed current state from its
   * REST source immediately instead of waiting for a 45 s SSE stall.
   */
  adopt: (operationId: string) => void;
  reset: () => void;
  disconnect: () => void;
  /**
   * Idempotent terminal latch. Returns `true` on the FIRST call (the caller
   * then runs its terminal side effects), `false` on every later call. On the
   * first fire it sets `isRunning=false`, closes the seed gate, and disconnects.
   */
  reportTerminal: () => boolean;
}

export function useOperationSSE(
  options: UseOperationSSEOptions,
): UseOperationSSEReturn {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  // True between an adopt() and the first recovery seed. Keeps the recovery gate
  // open immediately on reconnect (not only after a 45 s SSE stall).
  const [isSeeking, setIsSeeking] = useState(false);

  // First-writer-wins guard: terminal signals can race between SSE delivery and
  // the recovery fetch. The first to fire wins; the rest are no-ops.
  const terminalEmittedRef = useRef(false);

  // Hold callbacks in refs so the SSE effect only depends on operationId and
  // callers don't need to memoize.
  const onDomainEventRef = useRef(options.onDomainEvent);
  onDomainEventRef.current = options.onDomainEvent;
  const onResetRef = useRef(options.onReset);
  onResetRef.current = options.onReset;
  const onStreamEndRef = useRef(options.onStreamEnd);
  onStreamEndRef.current = options.onStreamEnd;
  // reportTerminal is defined below useSSEConnection (it needs disconnect); the
  // event handler reaches it through this ref to avoid a forward reference.
  const reportTerminalRef = useRef<() => boolean>(() => false);

  const {
    error: sseError,
    isConnected,
    disconnect,
    state: sseState,
    lastEventAt,
  } = useSSEConnection(operationId, {
    onEvent(eventType, data) {
      onDomainEventRef.current(
        eventType,
        data as Record<string, unknown>,
        reportTerminalRef.current,
      );
    },
    onStreamEnd() {
      // Only an *abnormal* close (no terminal latched) needs wrapper handling.
      // A normal terminal frame already fired reportTerminal before the stream
      // ended, so we skip a redundant failIfActive / snapshot reconcile.
      if (!terminalEmittedRef.current) onStreamEndRef.current?.();
    },
  });

  const reportTerminal = useCallback((): boolean => {
    if (terminalEmittedRef.current) return false;
    terminalEmittedRef.current = true;
    setIsRunning(false);
    setIsSeeking(false);
    disconnect();
    return true;
  }, [disconnect]);
  reportTerminalRef.current = reportTerminal;

  const begin = useCallback((opId: string, seed: boolean) => {
    onResetRef.current?.();
    terminalEmittedRef.current = false;
    setIsSeeking(seed);
    setOperationId(opId);
    setIsRunning(true);
  }, []);

  // Fresh run in this tab — SSE delivers from frame one, no seed needed.
  const start = useCallback((opId: string) => begin(opId, false), [begin]);
  // Re-attach to an in-flight run — open the recovery seed gate.
  const adopt = useCallback((opId: string) => begin(opId, true), [begin]);

  const reset = useCallback(() => {
    disconnect();
    onResetRef.current?.();
    terminalEmittedRef.current = false;
    setIsSeeking(false);
    setOperationId(null);
    setIsRunning(false);
  }, [disconnect]);

  const markSeeded = useCallback(() => setIsSeeking(false), []);

  const recovery: OperationSSERecovery = {
    // Derived from state, never the terminal ref — reading a ref during render
    // can tear/go stale. `isRunning` is flipped false by reportTerminal on the
    // terminal fire, so it closes the gate at the same moment the ref would.
    active: (isSeeking || sseState.kind === "stalled") && isRunning,
    markSeeded,
  };

  return {
    operationId,
    isRunning,
    isConnected,
    error: sseError,
    sseState,
    lastEventAt,
    recovery,
    start,
    adopt,
    reset,
    disconnect,
    reportTerminal,
  };
}
