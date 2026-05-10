/**
 * REST-based fallback for stalled SSE workflow runs.
 *
 * When the SSE watchdog flips state.kind === "stalled" (45 s without
 * any frame), poll GET /api/v1/operations/{id}/snapshot every 5 s to
 * recover terminal state from the persisted run row. Sweeper-marked
 * failed runs (heartbeat sweep) are surfaced here even when the
 * terminal SSE event was lost.
 *
 * The query is `enabled: state.kind === "stalled"` — it stops
 * automatically when SSE recovers (back to "streaming"). Tanstack
 * Query handles dedupe across consumers.
 */

import { useQuery } from "@tanstack/react-query";

import { customFetch } from "#/api/client";

export interface OperationSnapshotNode {
  node_id: string;
  node_type: string;
  status: string;
  execution_order: number;
  duration_ms?: number | null;
  input_track_count?: number | null;
  output_track_count?: number | null;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface OperationSnapshot {
  operation_id: string;
  run_id: string;
  workflow_id: string;
  status: string;
  is_terminal: boolean;
  error_message?: string | null;
  heartbeat_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  output_track_count?: number | null;
  duration_ms?: number | null;
  nodes: OperationSnapshotNode[];
}

export const SNAPSHOT_POLL_INTERVAL_MS = 5_000;

export function operationSnapshotQueryKey(operationId: string) {
  return ["operation-snapshot", operationId] as const;
}

export interface UseOperationSnapshotOptions {
  /** Disables the query. Used when SSE is healthy. */
  enabled: boolean;
  /** Poll cadence in ms. Defaults to 5 s. */
  refetchInterval?: number;
}

export function useOperationSnapshot(
  operationId: string | null,
  options: UseOperationSnapshotOptions,
) {
  return useQuery({
    queryKey: operationSnapshotQueryKey(operationId ?? "unknown"),
    queryFn: async (): Promise<OperationSnapshot> => {
      if (!operationId) throw new Error("operationId required");
      const envelope = await customFetch<{ data: OperationSnapshot }>(
        `/api/v1/operations/${operationId}/snapshot`,
      );
      return envelope.data;
    },
    enabled: options.enabled && operationId !== null,
    refetchInterval:
      options.enabled !== false
        ? (options.refetchInterval ?? SNAPSHOT_POLL_INTERVAL_MS)
        : false,
    // Don't retry on 404 — the operation_id is stable, retrying won't help.
    retry: false,
  });
}
