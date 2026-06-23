/**
 * Global import/sync operations awareness.
 *
 * Two jobs, split by how they scale:
 *   - **Failure surfacing** (poll-based, N-concurrent): diff the
 *     `?status=running` list across polls; when a run leaves the running set,
 *     fetch its terminal status and toast on failure — even if the user
 *     navigated away. Works for every in-flight operation, no SSE needed.
 *   - **Live re-attach** (single adopted op): `adopt(operationId)` streams one
 *     operation's live progress for a page that wants to resume it.
 *
 * Mounted INSIDE the router (it needs `useNavigate` for the toast action),
 * wrapping `<Routes>`. The sidebar badge does NOT consume this context — it
 * reads `useActiveOperations()` directly off the shared cache.
 *
 * One context (not the workflow provider's two-context split): the badge
 * bypasses it, leaving too few consumers to justify isolating SSE liveness.
 */

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  getOperationRunApiV1OperationRunsRunIdGet,
  retryFailedOperationApiV1OperationRunsRunIdRetryFailedPost,
} from "#/api/generated/operation-runs/operation-runs";
import { useActiveOperations } from "#/hooks/useActiveOperations";
import {
  type OperationProgress,
  useOperationProgressController,
} from "#/hooks/useOperationProgress";
import { claimRunToast } from "#/lib/operation-toast-ledger";
import { toasts } from "#/lib/toasts";

export interface OperationsState {
  /** All in-flight operation runs (server truth), for the sidebar/badge. */
  activeOperations: OperationRunSummarySchema[];
  /** The single op this provider is live-attached to, or null. */
  adoptedOperationId: string | null;
  /** Live progress for the adopted op, or null. */
  progress: OperationProgress | null;
  /** Re-attach live progress to an in-flight operation (single slot). */
  adopt: (operationId: string) => void;
  /** Detach from the adopted op. */
  reset: () => void;
}

const OperationsContext = createContext<OperationsState | null>(null);

/** Human noun for a terminal-failure toast, by operation_type prefix. */
function operationNoun(operationType: string): string {
  if (operationType.startsWith("import")) return "Import";
  if (operationType.startsWith("sync") || operationType.startsWith("apply")) {
    return "Sync";
  }
  return "Operation";
}

export function OperationsProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const { data: activeOperations = [] } = useActiveOperations();
  const liveProgress = useOperationProgressController();

  const [adoptedOperationId, setAdoptedOperationId] = useState<string | null>(
    null,
  );

  const { adopt: adoptLive, reset: resetLive } = liveProgress;
  const adopt = useCallback(
    (operationId: string) => {
      setAdoptedOperationId(operationId);
      adoptLive(operationId);
    },
    [adoptLive],
  );
  const reset = useCallback(() => {
    setAdoptedOperationId(null);
    resetLive();
  }, [resetLive]);

  // ── Poll-based failure surfacing ────────────────────────────────────────
  // Read the adopted id through a ref so surfaceTerminal stays stable (and the
  // diff effect only re-runs when the active list changes).
  const adoptedIdRef = useRef(adoptedOperationId);
  adoptedIdRef.current = adoptedOperationId;

  const viewLog = useCallback(
    (runId: string) => navigate(`/settings/imports?run=${runId}`),
    [navigate],
  );

  // Re-run only the failed items, then re-attach to the fresh operation. Falls
  // back to the log when the server says nothing is retryable (409).
  const retryFailed = useCallback(
    async (runId: string) => {
      try {
        const resp =
          await retryFailedOperationApiV1OperationRunsRunIdRetryFailedPost(
            runId,
          );
        if (resp.status === 202) {
          adopt(resp.data.operation_id);
          toasts.info("Retrying failed items…");
          return;
        }
      } catch {
        // fall through to the log
      }
      viewLog(runId);
    },
    [adopt, viewLog],
  );

  const surfaceTerminal = useCallback(
    async (runId: string) => {
      const resp = await getOperationRunApiV1OperationRunsRunIdGet(runId);
      if (resp.status !== 200) return;
      const run = resp.data;
      // Only failures toast; cancelled/superseded and clean completes don't.
      if (run.status !== "error") return;
      // The foreground card watching this op owns its toast.
      if (run.operation_id && run.operation_id === adoptedIdRef.current) return;
      // Shared ledger: skip if a foreground card already announced this run.
      if (!claimRunToast(runId)) return;

      const noun = operationNoun(run.operation_type);
      const failedCount = run.issues.length;
      const retryable =
        run.operation_type === "import_connector_playlists" && failedCount > 0;
      toasts.message(`${noun} failed`, {
        description:
          failedCount > 0
            ? `${failedCount} item${failedCount === 1 ? "" : "s"} failed.`
            : undefined,
        action: retryable
          ? { label: "Retry failed only", onClick: () => retryFailed(runId) }
          : { label: "View log", onClick: () => viewLog(runId) },
      });
    },
    [retryFailed, viewLog],
  );

  // Diff the running set across polls. A run that *left* the set → resolve its
  // terminal status. Seeded on the first poll so a pre-existing terminal
  // failure (predating mount) never retro-toasts.
  const prevRunningRef = useRef<Set<string> | null>(null);
  const resolvedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const running = new Set(activeOperations.map((r) => r.id));
    const prev = prevRunningRef.current;
    prevRunningRef.current = running;
    if (prev === null) return; // first poll: seed only

    for (const id of prev) {
      if (running.has(id) || resolvedRef.current.has(id)) continue;
      resolvedRef.current.add(id);
      void surfaceTerminal(id);
    }
  }, [activeOperations, surfaceTerminal]);

  const value = useMemo<OperationsState>(
    () => ({
      activeOperations,
      adoptedOperationId,
      progress: liveProgress.progress,
      adopt,
      reset,
    }),
    [activeOperations, adoptedOperationId, liveProgress.progress, adopt, reset],
  );

  return <OperationsContext value={value}>{children}</OperationsContext>;
}

export function useOperations(): OperationsState {
  const ctx = useContext(OperationsContext);
  if (!ctx) {
    throw new Error("useOperations must be used within OperationsProvider");
  }
  return ctx;
}
