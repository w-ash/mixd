/**
 * Durable truth for an import/sync operation, polled when SSE can't deliver it.
 *
 * The SSE queue is in-memory and dies with the machine; the `operation_runs`
 * row survives. When the stream is unresumable (bounded resume spent, or closed
 * with no terminal frame) this is what tells the UI how the run actually ended.
 *
 * Endpoint choice: the LIST endpoint (`GET /api/v1/operation-runs?type=all`),
 * filtered client-side on `operation_id`. The per-run detail endpoint is keyed
 * by `run_id`, which not every caller holds (chat cards and dialogs only ever
 * see the `operation_id` SSE handle), and the workflow `snapshot` endpoint
 * returns workflow_runs — no counts, no issues. The list row carries exactly
 * what the terminal UI needs: `status`, `counts`, and `issue_count`.
 */

import { useQuery } from "@tanstack/react-query";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import { OperationRunSummarySchemaStatus } from "#/api/generated/model";
import { listOperationRunsApiV1OperationRunsGet } from "#/api/generated/operation-runs/operation-runs";

/** Poll cadence while waiting for a run to reach a terminal status. */
export const RUN_ROW_POLL_INTERVAL_MS = 3_000;

/** One page is plenty: the run we lost the stream to is among the newest. */
const RUN_ROW_PAGE_LIMIT = 20;

const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  OperationRunSummarySchemaStatus.complete,
  OperationRunSummarySchemaStatus.partial,
  OperationRunSummarySchemaStatus.error,
  OperationRunSummarySchemaStatus.cancelled,
]);

export function isTerminalRunRow(row: OperationRunSummarySchema): boolean {
  return TERMINAL_RUN_STATUSES.has(row.status);
}

export function operationRunRowQueryKey(operationId: string) {
  return ["operation-run-row", operationId] as const;
}

export interface UseOperationRunRowOptions {
  /** Only poll while the stream can't answer. */
  enabled: boolean;
  /** Poll cadence in ms. Defaults to {@link RUN_ROW_POLL_INTERVAL_MS}. */
  refetchInterval?: number;
}

/**
 * The audit row for `operationId`, or null when the user has no such run yet.
 * Throws (→ `isError`) when the API itself is unreachable — the caller's cue
 * that the network, not just the stream, is down.
 */
export function useOperationRunRow(
  operationId: string | null,
  options: UseOperationRunRowOptions,
) {
  return useQuery({
    queryKey: operationRunRowQueryKey(operationId ?? "unknown"),
    queryFn: async (): Promise<OperationRunSummarySchema | null> => {
      if (!operationId) throw new Error("operationId required");
      const resp = await listOperationRunsApiV1OperationRunsGet({
        type: "all",
        limit: RUN_ROW_PAGE_LIMIT,
      });
      if (resp.status !== 200) {
        throw new Error(`operation-runs lookup failed: ${resp.status}`);
      }
      const rows = resp.data.data ?? [];
      return rows.find((row) => row.operation_id === operationId) ?? null;
    },
    enabled: options.enabled && operationId !== null,
    refetchInterval: options.enabled
      ? (options.refetchInterval ?? RUN_ROW_POLL_INTERVAL_MS)
      : false,
    // A stale row is worse than a round trip here — we're polling for a
    // transition, and retrying a hard failure just delays the honest verdict.
    staleTime: 0,
    retry: false,
  });
}
