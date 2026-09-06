import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { invalidateTags } from "#/api/cache-tags";
import type {
  OperationRunSummarySchema,
  SseFinalStatus,
  SseSubOperationOutcome,
} from "#/api/generated/model";
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

export type SubOperationOutcome = SseSubOperationOutcome;

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
  /** Durable-state poll cadence during recovery. Test seam. */
  recoveryPollIntervalMs?: number;
  /** SSE resume backoff. Test seam. */
  resumeDelayMs?: number;
}

interface UseOperationProgressResult {
  progress: OperationProgress | null;
  /** Whether the operation is currently running or pending. */
  isActive: boolean;
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

/** Fields an event contributes to a sub-operation row. Anything absent keeps
 *  what the row already holds — no single frame carries the whole picture. */
type SubOpPatch = Partial<Omit<SubOperationRecord, "operationId">>;

/** Fold one event's contribution into the row it belongs to. */
function mergeSubOpRecord(
  existing: SubOperationRecord | undefined,
  operationId: string,
  patch: SubOpPatch,
): SubOperationRecord {
  return {
    operationId,
    connectorPlaylistIdentifier:
      patch.connectorPlaylistIdentifier ??
      existing?.connectorPlaylistIdentifier ??
      null,
    playlistName: patch.playlistName ?? existing?.playlistName ?? null,
    outcome: patch.outcome ?? existing?.outcome ?? null,
    resolved: patch.resolved ?? existing?.resolved ?? null,
    unresolved: patch.unresolved ?? existing?.unresolved ?? null,
    errorMessage: patch.errorMessage ?? existing?.errorMessage ?? null,
    phase: patch.phase ?? existing?.phase ?? null,
    canonicalPlaylistId:
      patch.canonicalPlaylistId ?? existing?.canonicalPlaylistId ?? null,
    counts: patch.counts ?? existing?.counts ?? null,
  };
}

/**
 * Verdict a sub-operation's terminal status contributes to the results list.
 *
 * `null` means the item belongs in no column: a cancelled item neither
 * succeeded nor failed, and a non-terminal status cannot close a row at all.
 * Exhaustive over the wire enum, so a status added server-side fails the build
 * here rather than silently landing in the wrong column.
 */
const SUB_OPERATION_OUTCOME = {
  pending: null,
  running: null,
  completed: "succeeded",
  failed: "failed",
  crashed: "failed",
  cancelled: null,
} as const satisfies Record<SseFinalStatus, SubOperationOutcome | null>;

/** Statuses that can still change — anything the run hasn't concluded from. */
function isLive(status: OperationStatus): boolean {
  return (
    status === "pending" || status === "running" || status === "reconnecting"
  );
}

/** Whether the run has concluded — completed, failed or cancelled.
 *  A null progress is not terminal: the operation has not started. */
export function isTerminalProgress(
  progress: OperationProgress | null,
): boolean {
  return progress !== null && !isLive(progress.status);
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

  /**
   * Invalidate what the *server* says the operation staled.
   *
   * The producer is the only party that knows what it wrote, so the tags ride
   * the terminal frame (and the durable run row, for the recovery path) rather
   * than being guessed as a key list at each trigger callsite.
   */
  const applyTouched = useCallback(
    (touched: readonly string[] | null | undefined) => {
      if (touched?.length) void invalidateTags(queryClient, [...touched]);
    },
    [queryClient],
  );

  const core = useOperationSSE({
    resumeDelayMs: options?.resumeDelayMs,
    onReset: () => setProgress(null),
    // A stream that ends without a terminal frame is NOT a failed run — the
    // recovery gate opens and the durable row decides. See the poll below.
    onStreamEnd: () => setProgress(markRecovering(POLLING_MESSAGE)),
    onDomainEvent(event, reportTerminal) {
      switch (event.event) {
        case SSE_EVENT.STARTED: {
          const d = event.data;
          setProgress(
            initialProgress("running", d.description || "Starting...", {
              total: d.total ?? null,
              description: d.description ?? null,
            }),
          );
          break;
        }

        case SSE_EVENT.PROGRESS: {
          const d = event.data;
          setProgress((prev) => ({
            ...DEFAULT_PROGRESS,
            subOperationHistory: prev?.subOperationHistory ?? {},
            status: "running" as const,
            current: d.current,
            total: d.total ?? null,
            message: d.message || "Processing...",
            completionPercentage: d.completion_percentage ?? null,
            itemsPerSecond: d.items_per_second ?? null,
            etaSeconds: d.eta_seconds ?? null,
          }));
          break;
        }

        case SSE_EVENT.COMPLETE: {
          const d = event.data;
          if (reportTerminal()) {
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              subOperationHistory: prev?.subOperationHistory ?? {},
              status: "completed" as const,
              message: "Complete",
              counts: d.counts ?? prev?.counts ?? null,
              // Clear any still-active sub-op (ERROR does the same) so a lost
              // `sub_operation_completed` can't leave a spinning bar under the
              // "Complete" badge.
              subOperation: null,
            }));
            applyTouched(d.touched);
          }
          break;
        }

        case SSE_EVENT.ERROR: {
          const d = event.data;
          if (reportTerminal()) {
            setProgress((prev) => ({
              ...DEFAULT_PROGRESS,
              ...prev,
              subOperationHistory: prev?.subOperationHistory ?? {},
              status: "failed" as const,
              message: d.error_message ?? "Operation failed",
              counts: d.counts ?? prev?.counts ?? null,
              subOperation: null,
            }));
            applyTouched(d.touched);
          }
          break;
        }

        case SSE_EVENT.SUB_OPERATION_STARTED: {
          const d = event.data;
          const cid = d.connector_playlist_identifier ?? null;
          const opId = d.operation_id;
          const itemId = d.item_operation_id ?? null;
          const name = d.playlist_name ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, itemId, opId);
            return {
              ...prev,
              subOperation: {
                operationId: opId,
                itemOperationId: itemId,
                description: d.description,
                current: 0,
                total: d.total ?? null,
                message: d.description,
                phase: d.phase ?? null,
                completionPercentage: null,
                connectorPlaylistIdentifier: cid,
                playlistName: name,
              },
              subOperationHistory: {
                ...prev.subOperationHistory,
                // A nested phase starting is not the row restarting: the
                // patch names only what this frame knows.
                [key]: mergeSubOpRecord(
                  prev.subOperationHistory[key],
                  itemId ?? opId,
                  {
                    connectorPlaylistIdentifier: cid,
                    playlistName: name,
                    phase: d.phase,
                  },
                ),
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_PROGRESS: {
          const d = event.data;
          const cid = d.connector_playlist_identifier ?? null;
          const opId = d.operation_id;
          const itemId = d.item_operation_id ?? null;
          const name = d.playlist_name ?? null;
          setProgress((prev) => {
            if (!prev) return prev;
            const key = subOpKey(cid, itemId, opId);
            // Update live sub-op display if this event is for the active one.
            const sub = prev.subOperation;
            const updatedSub =
              sub &&
              (sub.operationId === opId ||
                (cid !== null && sub.connectorPlaylistIdentifier === cid))
                ? {
                    ...sub,
                    current: d.current,
                    total: d.total ?? sub.total,
                    message: d.message || sub.message,
                    completionPercentage: d.completion_percentage ?? null,
                    phase: d.phase ?? sub.phase,
                    playlistName: name ?? sub.playlistName,
                  }
                : sub;
            return {
              ...prev,
              subOperation: updatedSub,
              subOperationHistory: {
                ...prev.subOperationHistory,
                [key]: mergeSubOpRecord(
                  prev.subOperationHistory[key],
                  itemId ?? opId,
                  {
                    connectorPlaylistIdentifier: cid,
                    playlistName: name,
                    outcome: d.outcome,
                    resolved: d.resolved,
                    unresolved: d.unresolved,
                    errorMessage: d.error_message,
                    phase: d.phase,
                    canonicalPlaylistId: d.canonical_playlist_id,
                  },
                ),
              },
            };
          });
          break;
        }

        case SSE_EVENT.SUB_OPERATION_COMPLETED: {
          const d = event.data;
          const opId = d.operation_id;
          const itemId = d.item_operation_id ?? null;
          // An item that is itself a run signs off with counts, and this is
          // the only place they arrive — its own stream closes moments later.
          const counts = d.counts ?? null;
          const errorMessage = counts?.error_message;
          const outcome = SUB_OPERATION_OUTCOME[d.final_status];
          setProgress((prev) => {
            if (!prev) return prev;
            // A phase inside a row finishes with the same `completed` a row
            // does; only the item id separates them. Recording it would check
            // the row off — and wipe its counts — while it is still running.
            if (itemId !== null && itemId !== opId) return prev;
            // No verdict to record: retire the live row, leave the item out
            // of the succeeded/failed tallies.
            if (outcome === null) return { ...prev, subOperation: null };
            const key = subOpKey(null, itemId, opId);
            return {
              ...prev,
              subOperation: null,
              subOperationHistory: {
                ...prev.subOperationHistory,
                [key]: mergeSubOpRecord(
                  prev.subOperationHistory[key],
                  itemId ?? opId,
                  {
                    outcome,
                    errorMessage:
                      typeof errorMessage === "string" ? errorMessage : null,
                    counts,
                  },
                ),
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
        applyTouched(runRowData.touched);
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
    applyTouched,
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

  return { progress, isActive };
}
