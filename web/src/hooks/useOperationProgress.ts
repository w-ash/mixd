import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  isTerminalRunRow,
  useOperationRunRow,
} from "#/hooks/useOperationRunRow";
import { useOperationSSE } from "#/hooks/useOperationSSE";
import { SSE_EVENT } from "#/lib/sse-types";

export type OperationStatus =
  | "pending"
  | "running"
  /** Live updates were lost; the run itself is still being resolved. */
  | "reconnecting"
  | "completed"
  | "failed"
  | "cancelled";

export type SubOperationOutcome = "succeeded" | "skipped_unchanged" | "failed";

export interface SubOperationProgress {
  operationId: string;
  /** The row this belongs to — see `subOpKey`. Equals `operationId` for a
   * direct child, otherwise the ancestor row. */
  itemOperationId: string | null;
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
  /** Counts from this item's terminal event, when it is itself a run (an
   * import queue's per-file operations are). Null otherwise. */
  counts: Record<string, unknown> | null;
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
  /** Durable-state poll cadence during recovery. Test seam. */
  recoveryPollIntervalMs?: number;
  /** SSE resume backoff. Test seam. */
  resumeDelayMs?: number;
}

interface UseOperationProgressResult {
  progress: OperationProgress | null;
  /** Whether the operation is currently running or pending. */
  isActive: boolean;
  isConnected: boolean;
  error: Error | null;
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

/** Key a sub-op to the row that owns it.
 *
 * `connector_playlist_identifier` wins where it exists — stable across a
 * playlist's phases and what the playlist surfaces already look up by.
 * Otherwise `item_operation_id`, which the server sets to the ancestor that is
 * the direct child of the stream being read, so an event from a phase two
 * levels down still lands on its file's row. `operation_id` is the fallback.
 */
function subOpKey(
  connectorPlaylistIdentifier: string | null,
  itemOperationId: string | null,
  operationId: string,
): string {
  return connectorPlaylistIdentifier ?? itemOperationId ?? operationId;
}

/** Statuses that can still change — anything the run hasn't concluded from. */
function isLive(status: OperationStatus): boolean {
  return (
    status === "pending" || status === "running" || status === "reconnecting"
  );
}

/** Transition to failed only if the operation is still live.
 *  Guards against clobbering a terminal state (completed, failed, cancelled). */
function failIfActive(message: string) {
  return (prev: OperationProgress | null): OperationProgress | null =>
    prev && isLive(prev.status)
      ? {
          ...DEFAULT_PROGRESS,
          ...prev,
          subOperationHistory: prev.subOperationHistory,
          status: "failed" as const,
          message,
        }
      : prev;
}

/** Copy for the two honest intermediate states. Deliberately NOT
 *  "Connection failed": the run is still out there, and the durable row
 *  usually answers within seconds. */
const RESUMING_MESSAGE = "Connection lost — reconnecting…";
const POLLING_MESSAGE = "Connection lost — checking result…";

/** Flag the live-update channel as broken without claiming the run failed. */
function markRecovering(message: string) {
  return (prev: OperationProgress | null): OperationProgress | null =>
    prev && isLive(prev.status)
      ? { ...prev, status: "reconnecting" as const, message }
      : prev;
}

/** Terminal card state from the durable audit row — the same shape the SSE
 *  terminal frames produce, so every consumer's toast/badge logic is reused.
 *  A `partial` run rides its failure count in `counts.errors` exactly as the
 *  live `complete` event does, which is what `issueCountFromCounts` reads. */
function progressFromRunRow(row: OperationRunSummarySchema) {
  const counts: Record<string, unknown> = { ...row.counts };
  if (row.issue_count > 0) counts.errors = row.issue_count;

  const status: OperationStatus =
    row.status === "error"
      ? "failed"
      : row.status === "cancelled"
        ? "cancelled"
        : "completed";
  const message =
    row.status === "error"
      ? "Operation failed"
      : row.status === "cancelled"
        ? "Cancelled"
        : row.status === "partial"
          ? "Completed with issues"
          : "Complete";

  return (prev: OperationProgress | null): OperationProgress => ({
    ...DEFAULT_PROGRESS,
    ...prev,
    subOperationHistory: prev?.subOperationHistory ?? {},
    status,
    message,
    counts,
    subOperation: null,
  });
}

/** Give up after this many polls that find no audit row at all (~1 min at the
 *  default cadence). A run with a row that's still `running` keeps polling —
 *  that row is positive evidence the operation is alive. */
const MAX_ROWLESS_POLLS = 20;

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
 * Subscribes to real-time SSE progress for a given operation.
 *
 * Reduces SSE progress events (from the shared {@link useOperationSSE} core)
 * into an {@link OperationProgress} with per-sub-operation history, driven by
 * the `operationId` prop — pending state on connect, cleared on null. Connects
 * to GET /api/v1/operations/{operationId}/progress and invalidates the supplied
 * query keys when the operation completes or fails.
 *
 * Losing the stream never ends the run's story: the transport retries once with
 * `Last-Event-ID`, and if that can't resume, the hook polls the durable
 * `operation_runs` row and renders its terminal state through the same path a
 * live terminal frame would. "Connection failed" is reserved for the case where
 * neither the stream nor the API can be reached.
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

  const invalidateQueries = useCallback(() => {
    const keys = invalidateKeysRef.current;
    if (keys) {
      for (const key of keys) {
        queryClient.invalidateQueries({ queryKey: key as unknown[] });
      }
    }
  }, [queryClient]);

  const core = useOperationSSE({
    resumeDelayMs: options?.resumeDelayMs,
    onReset: () => setProgress(null),
    // A stream that ends without a terminal frame is NOT a failed run — the
    // recovery gate opens and the durable row decides. See the poll below.
    onStreamEnd: () => setProgress(markRecovering(POLLING_MESSAGE)),
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
              // Clear any still-active sub-op (ERROR does the same) so a lost
              // `sub_operation_completed` can't leave a spinning bar under the
              // "Complete" badge.
              subOperation: null,
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
          const itemId = (d.item_operation_id as string) ?? null;
          const name = (d.playlist_name as string) ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, itemId, opId);
            const existingRecord = prev.subOperationHistory[key];
            return {
              ...prev,
              subOperation: {
                operationId: opId,
                itemOperationId: itemId,
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
                  operationId: itemId ?? opId,
                  connectorPlaylistIdentifier: cid,
                  playlistName: name ?? existingRecord?.playlistName ?? null,
                  // A nested phase starting is not the row restarting.
                  outcome: existingRecord?.outcome ?? null,
                  resolved: existingRecord?.resolved ?? null,
                  unresolved: existingRecord?.unresolved ?? null,
                  errorMessage: existingRecord?.errorMessage ?? null,
                  phase: (d.phase as string) ?? existingRecord?.phase ?? null,
                  canonicalPlaylistId:
                    existingRecord?.canonicalPlaylistId ?? null,
                  counts: existingRecord?.counts ?? null,
                },
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_PROGRESS: {
          const cid = (d.connector_playlist_identifier as string) ?? null;
          const opId = (d.operation_id as string) ?? "";
          const itemId = (d.item_operation_id as string) ?? null;
          const name = (d.playlist_name as string) ?? null;
          const outcome = (d.outcome as SubOperationOutcome | null) ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, itemId, opId);
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
                  operationId: itemId ?? opId,
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
                  counts: existingRecord?.counts ?? null,
                },
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_OPERATION_COMPLETED: {
          const opId = (d.operation_id as string) ?? "";
          const itemId = (d.item_operation_id as string) ?? null;
          // An item that is itself a run signs off with counts, and this is
          // the only place they arrive — its own stream closes moments later.
          const finalStatus = (d.final_status as string) ?? null;
          const counts = (d.counts as Record<string, unknown>) ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            // A phase inside a row finishes with the same `completed` a row
            // does; only the item id separates them. Recording it would check
            // the row off — and wipe its counts — while it is still running.
            if (itemId !== null && itemId !== opId) return prev;
            if (finalStatus === null) {
              return { ...prev, subOperation: null };
            }
            const key = subOpKey(null, itemId, opId);
            const existing = prev.subOperationHistory[key];
            return {
              ...prev,
              subOperation: null,
              subOperationHistory: {
                ...prev.subOperationHistory,
                [key]: {
                  operationId: itemId ?? opId,
                  connectorPlaylistIdentifier:
                    existing?.connectorPlaylistIdentifier ?? null,
                  playlistName: existing?.playlistName ?? null,
                  outcome:
                    finalStatus === "failed"
                      ? ("failed" as const)
                      : ("succeeded" as const),
                  resolved: existing?.resolved ?? null,
                  unresolved: existing?.unresolved ?? null,
                  errorMessage:
                    (counts?.error_message as string | undefined) ??
                    existing?.errorMessage ??
                    null,
                  phase: existing?.phase ?? null,
                  canonicalPlaylistId: existing?.canonicalPlaylistId ?? null,
                  counts,
                },
              },
            };
          });
          break;
        }
      }
    },
  });

  // ─── Recovery: the stream is not the source of truth ─────────────
  //
  // A dead socket says nothing about the run. While the core's recovery gate is
  // open (bounded resume spent, stream closed with no terminal, or a 45 s
  // stall) poll the durable audit row and render its verdict through the same
  // terminal path the SSE frames use.

  const { reportTerminal } = core;
  const recoveryActive = core.recovery.active;
  const recoveryReason = core.recovery.reason;
  const sseKind = core.sseState.kind;

  const {
    data: runRowData,
    isError: runRowUnreachable,
    dataUpdatedAt: runRowUpdatedAt,
  } = useOperationRunRow(core.operationId, {
    enabled: recoveryActive,
    refetchInterval: options?.recoveryPollIntervalMs,
  });

  // Honest intermediate copy — the run is unresolved, not failed.
  useEffect(() => {
    if (sseKind === "resuming") setProgress(markRecovering(RESUMING_MESSAGE));
    else if (recoveryReason === "unresumable")
      setProgress(markRecovering(POLLING_MESSAGE));
  }, [sseKind, recoveryReason]);

  const rowlessPollsRef = useRef(0);
  useEffect(() => {
    if (!recoveryActive) rowlessPollsRef.current = 0;
  }, [recoveryActive]);

  useEffect(() => {
    if (!recoveryActive) return;
    // Only an unresumable stream can end in a dead end here. A stall or a
    // re-attach seed may still recover on its own, so those poll quietly.
    const isDeadEndCandidate = recoveryReason === "unresumable";

    // Stream gone AND the API unreachable: the only true dead end.
    if (runRowUnreachable) {
      if (isDeadEndCandidate && reportTerminal()) {
        setProgress(failIfActive("Connection failed"));
      }
      return;
    }
    // `dataUpdatedAt` is 0 until a poll lands, and moves on every poll after —
    // including ones that return the same row, which is what paces the
    // rowless counter below.
    if (runRowUpdatedAt === 0) return;

    if (runRowData && isTerminalRunRow(runRowData)) {
      if (reportTerminal()) {
        setProgress(progressFromRunRow(runRowData));
        invalidateQueries();
      }
      return;
    }
    // A row that's still `running` means the operation is alive — keep polling.
    if (runRowData) return;
    rowlessPollsRef.current += 1;
    if (
      isDeadEndCandidate &&
      rowlessPollsRef.current >= MAX_ROWLESS_POLLS &&
      reportTerminal()
    ) {
      setProgress(failIfActive("Connection failed"));
    }
  }, [
    recoveryActive,
    recoveryReason,
    runRowData,
    runRowUpdatedAt,
    runRowUnreachable,
    reportTerminal,
    invalidateQueries,
  ]);

  const { start: coreStart, reset: coreReset } = core;

  const start = useCallback(
    (opId: string) => {
      coreStart(opId);
      setProgress(initialProgress("pending", "Connecting..."));
    },
    [coreStart],
  );

  // Drive the lifecycle from the operationId prop.
  useEffect(() => {
    if (operationId) start(operationId);
    else coreReset();
  }, [operationId, start, coreReset]);

  // "reconnecting" counts as active: the run may still be going, so the
  // trigger stays disabled rather than inviting a duplicate launch.
  const isActive = progress !== null && isLive(progress.status);

  return {
    progress,
    isActive,
    isConnected: core.isConnected,
    error: core.error,
  };
}
