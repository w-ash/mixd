import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useSSEConnection } from "@/hooks/useSSEConnection";

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
  /** Whether the operation is currently running or pending. */
  isActive: boolean;
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

/** Transition to failed only if the operation is still active (pending/running).
 *  Guards against clobbering a terminal state (completed, failed, cancelled). */
function failIfActive(message: string) {
  return (prev: OperationProgress | null): OperationProgress | null =>
    prev && (prev.status === "pending" || prev.status === "running")
      ? { ...DEFAULT_PROGRESS, ...prev, status: "failed" as const, message }
      : prev;
}

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
  const queryClient = useQueryClient();

  // Stabilize invalidateKeys via ref to prevent infinite effect loops
  // when callers pass inline arrays (new reference each render).
  const invalidateKeysRef = useRef(options?.invalidateKeys);
  invalidateKeysRef.current = options?.invalidateKeys;

  const invalidateQueries = () => {
    const keys = invalidateKeysRef.current;
    if (keys) {
      for (const key of keys) {
        queryClient.invalidateQueries({ queryKey: key as unknown[] });
      }
    }
  };

  const { isConnected, error, disconnect } = useSSEConnection(operationId, {
    onStreamEnd() {
      setProgress(failIfActive("Connection lost"));
    },
    onEvent(eventType, data) {
      const d = data as Record<string, unknown>;

      switch (eventType) {
        case "started":
          setProgress({
            ...DEFAULT_PROGRESS,
            status: "running",
            total: (d.total as number) ?? null,
            message: (d.description as string) ?? "Starting...",
            description: (d.description as string) ?? null,
          });
          break;

        case "progress":
          setProgress({
            ...DEFAULT_PROGRESS,
            status: "running",
            current: (d.current as number) ?? 0,
            total: (d.total as number) ?? null,
            message: (d.message as string) ?? "Processing...",
            completionPercentage: (d.completion_percentage as number) ?? null,
            itemsPerSecond: (d.items_per_second as number) ?? null,
            etaSeconds: (d.eta_seconds as number) ?? null,
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
          disconnect();
          break;

        case "error":
          setProgress((prev) => ({
            ...DEFAULT_PROGRESS,
            ...prev,
            status: "failed" as const,
            message: (d.message as string) ?? "Operation failed",
            subOperation: null,
          }));
          invalidateQueries();
          disconnect();
          break;

        case "sub_operation_started":
          setProgress((prev) =>
            prev
              ? {
                  ...prev,
                  subOperation: {
                    operationId: (d.operation_id as string) ?? "",
                    description: (d.description as string) ?? "",
                    current: 0,
                    total: (d.total as number) ?? null,
                    message: (d.description as string) ?? "",
                    phase: (d.phase as string) ?? null,
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
                    current: (d.current as number) ?? prev.subOperation.current,
                    total: (d.total as number) ?? prev.subOperation.total,
                    message: (d.message as string) ?? prev.subOperation.message,
                    completionPercentage:
                      (d.completion_percentage as number) ?? null,
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
    },
  });

  // Domain-specific init: set pending state when operationId arrives, clear on null
  useEffect(() => {
    if (operationId) {
      setProgress({
        ...DEFAULT_PROGRESS,
        status: "pending",
        message: "Connecting...",
      });
    } else {
      setProgress(null);
    }
  }, [operationId]);

  // Transition to failed if the SSE transport errors (404, network failure, etc.)
  useEffect(() => {
    if (error) setProgress(failIfActive("Connection failed"));
  }, [error]);

  const isActive =
    progress?.status === "running" || progress?.status === "pending";

  return { progress, isActive, isConnected, error };
}
