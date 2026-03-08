/**
 * Shared SSE connection lifecycle hook.
 *
 * Owns the transport plumbing (AbortController, connectToSSE, event iteration,
 * AbortError suppression, malformed-JSON skip) so consumer hooks only handle
 * domain-specific event semantics via the onEvent callback.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { connectToSSE } from "@/api/sse-client";

export interface UseSSEConnectionOptions {
  /** Called for each parsed SSE event. eventType is the SSE "event" field, data is the parsed JSON. */
  onEvent: (eventType: string, data: unknown) => void;
  /** Called when the SSE stream ends normally (iterator exhausted). */
  onStreamEnd?: () => void;
}

export interface UseSSEConnectionReturn {
  isConnected: boolean;
  error: Error | null;
  disconnect: () => void;
}

export function useSSEConnection(
  operationId: string | null,
  options: UseSSEConnectionOptions,
): UseSSEConnectionReturn {
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Store callbacks in refs so the effect only depends on operationId.
  // Callers don't need to memoize their onEvent / onStreamEnd.
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;
  const onStreamEndRef = useRef(options.onStreamEnd);
  onStreamEndRef.current = options.onStreamEnd;

  const disconnect = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsConnected(false);
  }, []);

  useEffect(() => {
    if (!operationId) {
      setError(null);
      setIsConnected(false);
      return;
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setError(null);

    (async () => {
      try {
        const events = await connectToSSE(
          `/api/v1/operations/${operationId}/progress`,
          ctrl.signal,
        );
        setIsConnected(true);

        for await (const event of events) {
          if (!event.data) continue;

          try {
            const data = JSON.parse(event.data);
            onEventRef.current(event.event, data);
          } catch {
            // Skip malformed JSON — don't break the stream
          }
        }

        onStreamEndRef.current?.();
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err : new Error("SSE connection error"),
        );
        setIsConnected(false);
      }
    })();

    return disconnect;
  }, [operationId, disconnect]);

  return { isConnected, error, disconnect };
}
