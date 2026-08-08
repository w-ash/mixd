/**
 * Shared SSE connection lifecycle hook.
 *
 * Owns the transport plumbing (AbortController, connectToSSE, event iteration,
 * AbortError suppression, malformed-JSON skip) so consumer hooks only handle
 * domain-specific event semantics via the onEvent callback.
 *
 * Exposes a discriminated SSEState union and lastEventAt timestamp for
 * liveness UIs, plus a derived isConnected boolean kept for backwards
 * compatibility with consumers that only care about open/closed.
 *
 * The 45 s watchdog transitions streaming -> stalled when no frame
 * (including server keepalive comments) arrives. Comment frames bypass
 * the data guard but still bump lastEventAt — a server keepalive every
 * 15 s is enough to keep state in "streaming".
 *
 * A transport drop is retried ONCE (kind="resuming") after a short backoff,
 * replaying from the last received event id via `Last-Event-ID`. Only when
 * that retry also fails does the state reach "closed-error" — which callers
 * treat as "the stream is unrecoverable", not as "the run failed": the run's
 * durable state is still readable over REST.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { connectToSSE } from "#/api/sse-client";
import {
  SSE_MAX_RESUME_ATTEMPTS,
  SSE_RESUME_DELAY_MS,
  SSE_STALL_THRESHOLD_MS,
  SSE_WATCHDOG_TICK_MS,
  type SSEState,
} from "#/lib/sse-types";

export interface UseSSEConnectionOptions {
  /** Called for each parsed SSE event. eventType is the SSE "event" field, data is the parsed JSON. */
  onEvent: (eventType: string, data: unknown) => void;
  /** Called when the SSE stream ends normally (iterator exhausted). */
  onStreamEnd?: () => void;
  /** Backoff before the resume attempt. Defaults to {@link SSE_RESUME_DELAY_MS}. */
  resumeDelayMs?: number;
  /** Resume attempts after a drop. Defaults to {@link SSE_MAX_RESUME_ATTEMPTS} (one). */
  maxResumeAttempts?: number;
}

/** Abort-aware sleep. Resolves false when the signal fired (caller should bail). */
function delay(ms: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false);
  if (ms <= 0) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(!signal.aborted);
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export interface UseSSEConnectionReturn {
  /** Discriminated state for liveness UIs (pill, banner, watchdog). */
  state: SSEState;
  /** Most recent frame timestamp, or null if no frames yet / closed-done. */
  lastEventAt: number | null;
  /** Backwards-compatible: true when state is open (streaming/stalled/open-no-events). */
  isConnected: boolean;
  error: Error | null;
  disconnect: () => void;
}

function deriveLiveness(state: SSEState): {
  lastEventAt: number | null;
  isConnected: boolean;
} {
  const isConnected =
    state.kind === "streaming" ||
    state.kind === "stalled" ||
    state.kind === "open-no-events";
  switch (state.kind) {
    case "streaming":
    case "stalled":
    case "resuming":
    case "closed-error":
      return { lastEventAt: state.lastEventAt, isConnected };
    default:
      return { lastEventAt: null, isConnected };
  }
}

export function useSSEConnection(
  operationId: string | null,
  options: UseSSEConnectionOptions,
): UseSSEConnectionReturn {
  const [state, setState] = useState<SSEState>({ kind: "idle" });

  const abortRef = useRef<AbortController | null>(null);
  // Last event id seen on this operation's stream — the resume marker.
  const lastEventIdRef = useRef<string | null>(null);

  // Store callbacks/tunables in refs so the effect only depends on operationId.
  // Callers don't need to memoize their onEvent / onStreamEnd.
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;
  const onStreamEndRef = useRef(options.onStreamEnd);
  onStreamEndRef.current = options.onStreamEnd;
  const resumeDelayRef = useRef(options.resumeDelayMs);
  resumeDelayRef.current = options.resumeDelayMs;
  const maxResumeAttemptsRef = useRef(options.maxResumeAttempts);
  maxResumeAttemptsRef.current = options.maxResumeAttempts;

  const disconnect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setState((prev) =>
      prev.kind === "closed-done" || prev.kind === "closed-error"
        ? prev
        : { kind: "closed-done", finalAt: Date.now() },
    );
  }, []);

  // Watchdog: while streaming, transition to stalled if no frame for >45 s.
  useEffect(() => {
    if (state.kind !== "streaming") return;
    const id = setInterval(() => {
      setState((prev) => {
        if (prev.kind !== "streaming") return prev;
        const elapsed = Date.now() - prev.lastEventAt;
        if (elapsed <= SSE_STALL_THRESHOLD_MS) return prev;
        return {
          kind: "stalled",
          lastEventAt: prev.lastEventAt,
          since: Date.now(),
        };
      });
    }, SSE_WATCHDOG_TICK_MS);
    return () => clearInterval(id);
  }, [state.kind]);

  useEffect(() => {
    if (!operationId) {
      setState({ kind: "idle" });
      return;
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    lastEventIdRef.current = null;
    setState({ kind: "connecting" });

    (async () => {
      const url = `/api/v1/operations/${operationId}/progress`;
      const maxAttempts =
        maxResumeAttemptsRef.current ?? SSE_MAX_RESUME_ATTEMPTS;

      for (let attempt = 0; ; attempt++) {
        if (attempt > 0) {
          // Bounded resume: announce the window, wait out the backoff, then
          // reconnect asking the server to replay from the last event we saw.
          setState((prev) => ({
            kind: "resuming",
            attempt,
            lastEventAt: deriveLiveness(prev).lastEventAt,
            lastEventId: lastEventIdRef.current,
          }));
          const waited = await delay(
            resumeDelayRef.current ?? SSE_RESUME_DELAY_MS,
            ctrl.signal,
          );
          if (!waited) return;
        }

        try {
          const events =
            attempt === 0
              ? await connectToSSE(url, ctrl.signal)
              : await connectToSSE(url, ctrl.signal, {
                  lastEventId: lastEventIdRef.current,
                });
          // The effect cleanup aborts `ctrl` on unmount/disconnect. Bail before
          // every state write so a stream that resolves or keeps yielding after
          // teardown can't `setState` on an unmounted component — the source of a
          // stray cross-test "1 error" in the Vitest suite when a mocked iterator
          // ignores the abort signal.
          if (ctrl.signal.aborted) return;
          setState({ kind: "open-no-events", openedAt: Date.now() });

          for await (const event of events) {
            if (ctrl.signal.aborted) return;
            if (event.id) lastEventIdRef.current = event.id;
            // Bump freshness for every frame, including server keepalive
            // comments (which arrive as `event: ""` with empty data).
            // Done before the data guard so keepalives reset the watchdog.
            //
            // Snap to second-resolution: the freshness pill ticks at 1Hz
            // via useNow(1000), so finer-grained updates would only burn
            // React reconciliations without changing what the user sees.
            // Same-reference returns from the updater are skipped by React,
            // so a 4Hz sub_progress storm collapses to ~1 Hz of commits.
            const now = Date.now();
            const nowSecond = Math.floor(now / 1000);
            setState((prev) => {
              if (prev.kind === "closed-error" || prev.kind === "closed-done") {
                return prev;
              }
              if (
                prev.kind === "streaming" &&
                Math.floor(prev.lastEventAt / 1000) === nowSecond
              ) {
                return prev;
              }
              return { kind: "streaming", lastEventAt: now };
            });

            if (!event.data) continue;

            try {
              const data = JSON.parse(event.data);
              onEventRef.current(event.event, data);
            } catch {
              // Skip malformed JSON — don't break the stream
            }
          }

          if (ctrl.signal.aborted) return;
          setState((prev) =>
            prev.kind === "closed-error" || prev.kind === "closed-done"
              ? prev
              : { kind: "closed-done", finalAt: Date.now() },
          );
          onStreamEndRef.current?.();
          return;
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          if (ctrl.signal.aborted) return;
          // Retry budget left → loop and resume. A 404 here means the server's
          // in-memory queue is gone (run ended, or the machine restarted); it
          // exhausts the budget like any other drop, and the caller falls
          // through to the durable REST state rather than declaring failure.
          if (attempt < maxAttempts) continue;
          const error =
            err instanceof Error ? err : new Error("SSE connection error");
          setState((prev) => ({
            kind: "closed-error",
            error,
            lastEventAt: deriveLiveness(prev).lastEventAt,
          }));
          return;
        }
      }
    })();

    return () => {
      ctrl.abort();
      abortRef.current = null;
    };
  }, [operationId]);

  const { lastEventAt, isConnected } = useMemo(
    () => deriveLiveness(state),
    [state],
  );
  const error = state.kind === "closed-error" ? state.error : null;

  return { state, lastEventAt, isConnected, error, disconnect };
}
