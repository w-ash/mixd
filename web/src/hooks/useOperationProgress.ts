import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { connectToSSE, type SSEEvent } from "@/api/sse-client";

export type OperationStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface SubOperationProgress {
  operationId: string;
  description: string;
  current: number;
  total: number | null;
  message: string;
  phase: string | null;
  completionPercentage: number | null;
}

export interface OperationProgress {
  status: OperationStatus;
  current: number;
  total: number | null;
  message: string;
  description: string | null;
  completionPercentage: number | null;
  itemsPerSecond: number | null;
  etaSeconds: number | null;
  subOperation: SubOperationProgress | null;
}

export interface UseOperationProgressOptions {
  /** Query keys to invalidate when the operation completes or fails. */
  invalidateKeys?: readonly (readonly unknown[])[];
}

interface UseOperationProgressResult {
  progress: OperationProgress | null;
  isConnected: boolean;
  error: Error | null;
}

/** Shared zero-state for fields that don't vary across event handlers. */
const DEFAULT_PROGRESS: Omit<OperationProgress, "status" | "message"> = {
  current: 0,
  total: null,
  description: null,
  completionPercentage: null,
  itemsPerSecond: null,
  etaSeconds: null,
  subOperation: null,
};

/**
 * Subscribes to real-time SSE progress for a given operation.
 *
 * Connects to GET /api/v1/operations/{operationId}/progress and parses
 * typed progress events. Cleans up on unmount or operationId change.
 * Invalidates specified query keys when the operation completes.
 */
export function useOperationProgress(
  operationId: string | null,
  options?: UseOperationProgressOptions,
): UseOperationProgressResult {
  const [progress, setProgress] = useState<OperationProgress | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  // Stabilize invalidateKeys via ref to prevent infinite effect loops
  // when callers pass inline arrays (new reference each render).
  const invalidateKeysRef = useRef(options?.invalidateKeys);
  invalidateKeysRef.current = options?.invalidateKeys;

  const cleanup = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsConnected(false);
  }, []);

  useEffect(() => {
    if (!operationId) {
      setProgress(null);
      setError(null);
      cleanup();
      return;
    }

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setProgress({
      ...DEFAULT_PROGRESS,
      status: "pending",
      message: "Connecting...",
    });
    setError(null);

    const invalidateQueries = () => {
      const keys = invalidateKeysRef.current;
      if (keys) {
        for (const key of keys) {
          queryClient.invalidateQueries({ queryKey: key as unknown[] });
        }
      }
    };

    const processEvent = (event: SSEEvent) => {
      if (!event.data) return;

      try {
        const data = JSON.parse(event.data);

        switch (event.event) {
          case "started":
            setProgress({
              ...DEFAULT_PROGRESS,
              status: "running",
              total: data.total ?? null,
              message: data.description ?? "Starting...",
              description: data.description ?? null,
            });
            break;

          case "progress":
            setProgress({
              ...DEFAULT_PROGRESS,
              status: "running",
              current: data.current ?? 0,
              total: data.total ?? null,
              message: data.message ?? "Processing...",
              completionPercentage: data.completion_percentage ?? null,
              itemsPerSecond: data.items_per_second ?? null,
              etaSeconds: data.eta_seconds ?? null,
            });
            break;

          case "complete":
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              status: "completed" as const,
              message: "Complete",
            }));
            invalidateQueries();
            cleanup();
            break;

          case "error":
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              status: "failed" as const,
              message: data.message ?? "Operation failed",
              subOperation: null,
            }));
            invalidateQueries();
            cleanup();
            break;

          case "sub_operation_started":
            setProgress((prev) =>
              prev
                ? {
                    ...prev,
                    subOperation: {
                      operationId: data.operation_id ?? "",
                      description: data.description ?? "",
                      current: 0,
                      total: data.total ?? null,
                      message: data.description ?? "",
                      phase: data.phase ?? null,
                      completionPercentage: null,
                    },
                  }
                : prev,
            );
            break;

          case "sub_progress":
            setProgress((prev) =>
              prev?.subOperation
                ? {
                    ...prev,
                    subOperation: {
                      ...prev.subOperation,
                      current: data.current ?? prev.subOperation.current,
                      total: data.total ?? prev.subOperation.total,
                      message: data.message ?? prev.subOperation.message,
                      completionPercentage: data.completion_percentage ?? null,
                    },
                  }
                : prev,
            );
            break;

          case "sub_operation_completed":
            setProgress((prev) =>
              prev ? { ...prev, subOperation: null } : prev,
            );
            break;
        }
      } catch {
        // Ignore malformed events
      }
    };

    (async () => {
      try {
        const events = await connectToSSE(
          `/api/v1/operations/${operationId}/progress`,
          ctrl.signal,
        );
        setIsConnected(true);
        setError(null);

        for await (const event of events) {
          processEvent(event);
        }
      } catch (err) {
        // AbortError is expected during cleanup — don't surface it
        if (err instanceof DOMException && err.name === "AbortError") return;

        setError(
          err instanceof Error ? err : new Error("SSE connection error"),
        );
        setIsConnected(false);
      }
    })();

    return cleanup;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- invalidateKeys
    // is read via ref to prevent infinite loops from unstable references.
  }, [operationId, queryClient, cleanup]);

  return { progress, isConnected, error };
}
