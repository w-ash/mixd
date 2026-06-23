import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { useOperationSSE } from "#/hooks/useOperationSSE";
import { SSE_EVENT, type SSEState } from "#/lib/sse-types";

export type OperationStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type SubOperationOutcome = "succeeded" | "skipped_unchanged" | "failed";

export interface SubOperationProgress {
  operationId: string;
  description: string;
  current: number;
  total: number | null;
  message: string;
  phase: string | null;
  completionPercentage: number | null;
  /** Provider-native id (e.g. Spotify playlist id). Stable across events. */
  connectorPlaylistIdentifier: string | null;
  /** Real playlist name (fills in from first sub_progress once known). */
  playlistName: string | null;
}

export interface SubOperationRecord {
  operationId: string;
  connectorPlaylistIdentifier: string | null;
  playlistName: string | null;
  outcome: SubOperationOutcome | null;
  resolved: number | null;
  unresolved: number | null;
  errorMessage: string | null;
  phase: string | null;
  canonicalPlaylistId: string | null;
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
  /** Per-operation summary counts from the terminal event — the
   * backend ``summary_metrics`` names (``track_plays``, ``imported``,
   * ``exported``, ``errors``, …). Null until the operation completes/fails. */
  counts: Record<string, unknown> | null;
  /** The currently-active sub-operation (if any). Cleared on completion. */
  subOperation: SubOperationProgress | null;
  /** Every completed sub-op keyed by connector_playlist_identifier (or
   * operation_id when no identifier was provided). Accumulates across the
   * run so the UI can render a per-playlist results list. */
  subOperationHistory: Record<string, SubOperationRecord>;
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

/** Imperative re-attach controller: like {@link useOperationProgress} but the
 * caller drives the operation lifecycle (`start`/`adopt`/`reset`) instead of
 * passing an operationId prop. Used by the global operations provider to
 * re-attach to an in-flight import/sync. */
interface UseOperationProgressController extends UseOperationProgressResult {
  sseState: SSEState;
  lastEventAt: number | null;
  start: (operationId: string) => void;
  adopt: (operationId: string) => void;
  reset: () => void;
}

/** Shared zero-state for fields that don't vary across event handlers.
 * ``subOperationHistory`` is intentionally omitted — its accumulator must
 * survive the reducer-style spreads that rebuild the rest of the state. */
const DEFAULT_PROGRESS: Omit<
  OperationProgress,
  "status" | "message" | "subOperationHistory"
> = {
  current: 0,
  total: null,
  description: null,
  completionPercentage: null,
  itemsPerSecond: null,
  etaSeconds: null,
  counts: null,
  subOperation: null,
};

/** Key a sub-op by connector_playlist_identifier when present (stable across
 * the fetch → resolve → done phases), falling back to operation_id when the
 * event carries no identifier. */
function subOpKey(
  connectorPlaylistIdentifier: string | null,
  operationId: string,
): string {
  return connectorPlaylistIdentifier ?? operationId;
}

/** Transition to failed only if the operation is still active (pending/running).
 *  Guards against clobbering a terminal state (completed, failed, cancelled). */
function failIfActive(message: string) {
  return (prev: OperationProgress | null): OperationProgress | null =>
    prev && (prev.status === "pending" || prev.status === "running")
      ? {
          ...DEFAULT_PROGRESS,
          ...prev,
          subOperationHistory: prev.subOperationHistory,
          status: "failed" as const,
          message,
        }
      : prev;
}

/** Build a fresh zero-state keyed progress object. */
function initialProgress(
  status: OperationStatus,
  message: string,
  overrides: Partial<OperationProgress> = {},
): OperationProgress {
  return {
    ...DEFAULT_PROGRESS,
    status,
    message,
    subOperationHistory: {},
    ...overrides,
  };
}

/**
 * Imperative controller over the shared {@link useOperationSSE} core, reducing
 * SSE progress events into an {@link OperationProgress} with per-sub-operation
 * history. The caller owns the lifecycle via `start`/`adopt`/`reset`.
 *
 * Connects to GET /api/v1/operations/{operationId}/progress and invalidates the
 * specified query keys when the operation completes or fails.
 */
export function useOperationProgressController(
  options?: UseOperationProgressOptions,
): UseOperationProgressController {
  const [progress, setProgress] = useState<OperationProgress | null>(null);
  const queryClient = useQueryClient();

  // Stabilize invalidateKeys via ref to prevent infinite effect loops
  // when callers pass inline arrays (new reference each render).
  const invalidateKeysRef = useRef(options?.invalidateKeys);
  invalidateKeysRef.current = options?.invalidateKeys;

  const invalidateQueries = useCallback(() => {
    const keys = invalidateKeysRef.current;
    if (keys) {
      for (const key of keys) {
        queryClient.invalidateQueries({ queryKey: key as unknown[] });
      }
    }
  }, [queryClient]);

  const core = useOperationSSE({
    onReset: () => setProgress(null),
    onStreamEnd: () => setProgress(failIfActive("Connection lost")),
    onDomainEvent(eventType, d, reportTerminal) {
      switch (eventType) {
        case SSE_EVENT.STARTED:
          setProgress(
            initialProgress(
              "running",
              (d.description as string) ?? "Starting...",
              {
                total: (d.total as number) ?? null,
                description: (d.description as string) ?? null,
              },
            ),
          );
          break;

        case SSE_EVENT.PROGRESS:
          setProgress((prev) => ({
            ...DEFAULT_PROGRESS,
            subOperationHistory: prev?.subOperationHistory ?? {},
            status: "running" as const,
            current: (d.current as number) ?? 0,
            total: (d.total as number) ?? null,
            message: (d.message as string) ?? "Processing...",
            completionPercentage: (d.completion_percentage as number) ?? null,
            itemsPerSecond: (d.items_per_second as number) ?? null,
            etaSeconds: (d.eta_seconds as number) ?? null,
          }));
          break;

        case SSE_EVENT.COMPLETE:
          if (reportTerminal()) {
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              subOperationHistory: prev?.subOperationHistory ?? {},
              status: "completed" as const,
              message: "Complete",
              counts:
                (d.counts as Record<string, unknown>) ?? prev?.counts ?? null,
            }));
            invalidateQueries();
          }
          break;

        case SSE_EVENT.ERROR:
          if (reportTerminal()) {
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              subOperationHistory: prev?.subOperationHistory ?? {},
              status: "failed" as const,
              message: (d.message as string) ?? "Operation failed",
              counts:
                (d.counts as Record<string, unknown>) ?? prev?.counts ?? null,
              subOperation: null,
            }));
            invalidateQueries();
          }
          break;

        case SSE_EVENT.SUB_OPERATION_STARTED: {
          const cid = (d.connector_playlist_identifier as string) ?? null;
          const opId = (d.operation_id as string) ?? "";
          const name = (d.playlist_name as string) ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, opId);
            return {
              ...prev,
              subOperation: {
                operationId: opId,
                description: (d.description as string) ?? "",
                current: 0,
                total: (d.total as number) ?? null,
                message: (d.description as string) ?? "",
                phase: (d.phase as string) ?? null,
                completionPercentage: null,
                connectorPlaylistIdentifier: cid,
                playlistName: name,
              },
              subOperationHistory: {
                ...prev.subOperationHistory,
                [key]: {
                  operationId: opId,
                  connectorPlaylistIdentifier: cid,
                  playlistName: name,
                  outcome: null,
                  resolved: null,
                  unresolved: null,
                  errorMessage: null,
                  phase: (d.phase as string) ?? null,
                  canonicalPlaylistId: null,
                },
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_PROGRESS: {
          const cid = (d.connector_playlist_identifier as string) ?? null;
          const opId = (d.operation_id as string) ?? "";
          const name = (d.playlist_name as string) ?? null;
          const outcome = (d.outcome as SubOperationOutcome | null) ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, opId);
            const existingRecord = prev.subOperationHistory[key];
            // Update live sub-op display if this event is for the active one.
            const sub = prev.subOperation;
            const updatedSub =
              sub &&
              (sub.operationId === opId ||
                (cid !== null && sub.connectorPlaylistIdentifier === cid))
                ? {
                    ...sub,
                    current: (d.current as number) ?? sub.current,
                    total: (d.total as number) ?? sub.total,
                    message: (d.message as string) ?? sub.message,
                    completionPercentage:
                      (d.completion_percentage as number) ?? null,
                    phase: (d.phase as string) ?? sub.phase,
                    playlistName: name ?? sub.playlistName,
                  }
                : sub;
            return {
              ...prev,
              subOperation: updatedSub,
              subOperationHistory: {
                ...prev.subOperationHistory,
                [key]: {
                  operationId: opId,
                  connectorPlaylistIdentifier:
                    cid ?? existingRecord?.connectorPlaylistIdentifier ?? null,
                  playlistName: name ?? existingRecord?.playlistName ?? null,
                  outcome: outcome ?? existingRecord?.outcome ?? null,
                  resolved:
                    (d.resolved as number | null) ??
                    existingRecord?.resolved ??
                    null,
                  unresolved:
                    (d.unresolved as number | null) ??
                    existingRecord?.unresolved ??
                    null,
                  errorMessage:
                    (d.error_message as string | null) ??
                    existingRecord?.errorMessage ??
                    null,
                  phase:
                    (d.phase as string | null) ?? existingRecord?.phase ?? null,
                  canonicalPlaylistId:
                    (d.canonical_playlist_id as string | null) ??
                    existingRecord?.canonicalPlaylistId ??
                    null,
                },
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_OPERATION_COMPLETED:
          setProgress((prev) =>
            prev ? { ...prev, subOperation: null } : prev,
          );
          break;
      }
    },
  });

  // Transition to failed if the SSE transport errors (404, network failure, etc.)
  useEffect(() => {
    if (core.error) setProgress(failIfActive("Connection failed"));
  }, [core.error]);

  const { start: coreStart, adopt: coreAdopt, reset } = core;
  const { markSeeded } = core.recovery;

  const start = useCallback(
    (operationId: string) => {
      coreStart(operationId);
      setProgress(initialProgress("pending", "Connecting..."));
    },
    [coreStart],
  );

  const adopt = useCallback(
    (operationId: string) => {
      coreAdopt(operationId);
      // The /operations/{id}/snapshot endpoint is workflow-only (404s for an
      // import/sync), so there's no persisted seed to wait for — close the gate
      // and resume from the live stream. A terminal-after-reload seed off the
      // operation-run row lands with the operations provider.
      markSeeded();
      setProgress(initialProgress("pending", "Connecting..."));
    },
    [coreAdopt, markSeeded],
  );

  const isActive =
    progress?.status === "running" || progress?.status === "pending";

  return {
    progress,
    isActive,
    isConnected: core.isConnected,
    error: core.error,
    sseState: core.sseState,
    lastEventAt: core.lastEventAt,
    start,
    adopt,
    reset,
  };
}

/**
 * Subscribes to real-time SSE progress for a given operation.
 *
 * Connects to GET /api/v1/operations/{operationId}/progress and parses typed
 * progress events, driven by the `operationId` prop (pending state on connect,
 * cleared on null). For imperative re-attach use
 * {@link useOperationProgressController}.
 */
export function useOperationProgress(
  operationId: string | null,
  options?: UseOperationProgressOptions,
): UseOperationProgressResult {
  const { progress, isActive, isConnected, error, start, reset } =
    useOperationProgressController(options);

  // Domain-specific init: drive the controller from the operationId prop.
  useEffect(() => {
    if (operationId) start(operationId);
    else reset();
  }, [operationId, start, reset]);

  return { progress, isActive, isConnected, error };
}
